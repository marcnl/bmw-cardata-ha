"""Config and options flow for BMW CarData."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .auth import (
    CardataAuthError,
    async_poll_for_tokens,
    async_request_device_code,
    generate_pkce_pair,
)
from .catalogue import GROUP_LABELS
from .const import (
    CONF_CLIENT_ID,
    DEFAULT_DESCRIPTOR_GROUPS,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MIN_POLL_INTERVAL,
    OPT_DESCRIPTOR_GROUPS,
    OPT_ENABLE_CHARGING_HISTORY,
    OPT_ENABLE_STREAM,
    OPT_ENABLE_TYRE_DIAGNOSIS,
    OPT_POLL_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class CardataConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._client_id: str | None = None
        self._verifier: str | None = None
        self._device: dict[str, Any] | None = None
        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._client_id = user_input[CONF_CLIENT_ID].strip()
            try:
                await self._request_device_code()
            except CardataAuthError as err:
                errors["base"] = "device_code_failed"
                _LOGGER.warning("Device code request failed: %s", err)
            else:
                return await self.async_step_authorize()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_CLIENT_ID): str}),
            errors=errors,
            description_placeholders={
                "portal": "https://bmw-cardata.bmwgroup.com/customer"
            },
        )

    async def _request_device_code(self) -> None:
        assert self._client_id
        self._verifier, challenge = generate_pkce_pair()
        session = async_get_clientsession(self.hass)
        self._device = await async_request_device_code(
            session, client_id=self._client_id, code_challenge=challenge
        )

    async def async_step_authorize(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._device and self._client_id and self._verifier
        placeholders = {
            "url": self._device.get("verification_uri_complete")
            or self._device.get("verification_uri", ""),
            "code": self._device.get("user_code", ""),
        }
        if user_input is None:
            return self.async_show_form(
                step_id="authorize",
                data_schema=vol.Schema({}),
                description_placeholders=placeholders,
            )

        session = async_get_clientsession(self.hass)
        try:
            tokens = await async_poll_for_tokens(
                session,
                client_id=self._client_id,
                device_code=self._device["device_code"],
                code_verifier=self._verifier,
                interval=int(self._device.get("interval", 5)),
                expires_in=int(self._device.get("expires_in", 300)),
            )
        except CardataAuthError as err:
            _LOGGER.warning("Authorization failed: %s", err)
            return self.async_show_form(
                step_id="authorize",
                data_schema=vol.Schema({}),
                errors={"base": "authorization_failed"},
                description_placeholders=placeholders,
            )

        data = {CONF_CLIENT_ID: self._client_id, **tokens.as_dict()}
        await self.async_set_unique_id(tokens.gcid or self._client_id)

        if self._reauth_entry:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(
                self._reauth_entry, data={**self._reauth_entry.data, **data}
            )

        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="BMW CarData", data=data)

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        self._reauth_entry = self._get_reauth_entry()
        self._client_id = entry_data.get(CONF_CLIENT_ID)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm", data_schema=vol.Schema({})
            )
        try:
            await self._request_device_code()
        except CardataAuthError:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema({}),
                errors={"base": "device_code_failed"},
            )
        return await self.async_step_authorize()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CardataOptionsFlow()


class CardataOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        opts = self.config_entry.options
        group_options = [
            SelectOptionDict(value=key, label=label)
            for key, label in GROUP_LABELS.items()
        ]
        schema = vol.Schema(
            {
                vol.Required(
                    OPT_DESCRIPTOR_GROUPS,
                    default=opts.get(OPT_DESCRIPTOR_GROUPS, DEFAULT_DESCRIPTOR_GROUPS),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=group_options,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(
                    OPT_POLL_INTERVAL,
                    default=opts.get(OPT_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_POLL_INTERVAL, max=6 * 3600, step=60, unit_of_measurement="s"
                    )
                ),
                vol.Required(
                    OPT_ENABLE_STREAM, default=opts.get(OPT_ENABLE_STREAM, True)
                ): BooleanSelector(),
                vol.Required(
                    OPT_ENABLE_CHARGING_HISTORY,
                    default=opts.get(OPT_ENABLE_CHARGING_HISTORY, False),
                ): BooleanSelector(),
                vol.Required(
                    OPT_ENABLE_TYRE_DIAGNOSIS,
                    default=opts.get(OPT_ENABLE_TYRE_DIAGNOSIS, False),
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
