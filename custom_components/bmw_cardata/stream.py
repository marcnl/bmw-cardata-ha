"""BMW CarData MQTT streaming client.

Connects to the CarData broker with the GCID as username/client-id and the
current OAuth ``id_token`` as password. The broker drops the connection when the
id_token expires (~hourly), so callers push a fresh token via
``async_update_token`` and we reconnect.

Only one connection per GCID is allowed by BMW -- running another CarData client
(a second HA integration, MQTTX, ...) against the same account will cause both to
flap.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from collections.abc import Awaitable, Callable

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from homeassistant.core import HomeAssistant

from .const import STREAM_HOST, STREAM_KEEPALIVE, STREAM_PORT

_LOGGER = logging.getLogger(__name__)

MessageCallback = Callable[[dict], None]
StatusCallback = Callable[[str, str | None], None]


class CardataStream:
    """Manage the CarData MQTT connection lifecycle."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        gcid: str,
        id_token: str,
        on_message: MessageCallback,
        on_status: StatusCallback,
    ) -> None:
        self.hass = hass
        self._gcid = gcid
        self._id_token = id_token
        self._on_message = on_message
        self._on_status = on_status
        self._client: mqtt.Client | None = None
        self._started = False
        self._reconnect_task: asyncio.Task | None = None
        self._backoff = 5
        self._lock = asyncio.Lock()

    async def async_start(self) -> None:
        async with self._lock:
            self._started = True
            await self._connect()

    async def async_stop(self) -> None:
        async with self._lock:
            self._started = False
            if self._reconnect_task:
                self._reconnect_task.cancel()
                self._reconnect_task = None
            await self._disconnect()

    async def async_update_token(self, id_token: str) -> None:
        """Swap the id_token (password) and reconnect if it changed."""
        async with self._lock:
            if id_token == self._id_token:
                return
            self._id_token = id_token
            if self._started:
                await self._disconnect()
                await self._connect()

    # --- internals ---------------------------------------------------
    async def _connect(self) -> None:
        await self.hass.async_add_executor_job(self._build_and_connect)

    def _build_and_connect(self) -> None:
        client = mqtt.Client(
            CallbackAPIVersion.VERSION2,
            client_id=self._gcid,
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )
        client.username_pw_set(self._gcid, self._id_token)
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        client.tls_set_context(context)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_mqtt_message
        client.reconnect_delay_set(min_delay=5, max_delay=120)
        try:
            client.connect(STREAM_HOST, STREAM_PORT, keepalive=STREAM_KEEPALIVE)
        except OSError as err:
            _LOGGER.error("BMW CarData stream connect failed: %s", err)
            self._notify_status("error", str(err))
            self.hass.loop.call_soon_threadsafe(self._schedule_reconnect)
            return
        client.loop_start()
        self._client = client

    async def _disconnect(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        await self.hass.async_add_executor_job(_stop_client, client)

    def _schedule_reconnect(self) -> None:
        if not self._started or (self._reconnect_task and not self._reconnect_task.done()):
            return

        async def _run() -> None:
            try:
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, 300)
                async with self._lock:
                    if self._started:
                        await self._disconnect()
                        await self._connect()
            except asyncio.CancelledError:
                pass
            finally:
                self._reconnect_task = None

        self._reconnect_task = self.hass.loop.create_task(_run())

    # --- paho callbacks (executor thread) --------------------------
    def _on_connect(self, client: mqtt.Client, userdata, flags, reason_code, properties=None) -> None:
        if reason_code == 0 or getattr(reason_code, "is_failure", True) is False:
            self._backoff = 5
            client.subscribe(f"{self._gcid}/+")
            _LOGGER.info("BMW CarData stream connected")
            self._notify_status("connected", None)
        else:
            _LOGGER.warning("BMW CarData stream refused: %s", reason_code)
            self._notify_status("unauthorized", str(reason_code))
            self.hass.loop.call_soon_threadsafe(self._schedule_reconnect)

    def _on_disconnect(self, client: mqtt.Client, userdata, *args) -> None:
        reason_code = args[-2] if len(args) >= 2 else (args[0] if args else None)
        _LOGGER.warning("BMW CarData stream disconnected: %s", reason_code)
        self._notify_status("disconnected", str(reason_code))
        if self._started:
            self.hass.loop.call_soon_threadsafe(self._schedule_reconnect)

    def _on_mqtt_message(self, client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
        try:
            data = json.loads(msg.payload.decode("utf-8", errors="ignore"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            _LOGGER.debug("Ignoring unparseable stream payload on %s", msg.topic)
            return
        if isinstance(data, dict):
            self.hass.loop.call_soon_threadsafe(self._on_message, data)

    def _notify_status(self, status: str, reason: str | None) -> None:
        self.hass.loop.call_soon_threadsafe(self._on_status, status, reason)


def _stop_client(client: mqtt.Client) -> None:
    try:
        client.disconnect()
        client.loop_stop()
    except Exception:  # noqa: BLE001 - best-effort teardown
        pass
