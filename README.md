# BMW CarData for Home Assistant

A Home Assistant custom integration for **[BMW CarData](https://bmw-cardata.bmwgroup.com/customer)**,
BMW's official successor to the retired ConnectedDrive data feed.

Unlike other CarData integrations, this one requests the **full service &
inspection descriptor set** by default (Next Service, Next Inspection, Condition
Based Services, teleservice history, …) in addition to core vehicle status
(mileage, doors, windows, locks, tyre pressures, location), and creates entities
for whatever your specific vehicle actually reports.

## How it works

- **OAuth 2.0 Device Code Flow** (with PKCE) — you paste a *Client ID* from the
  BMW CarData portal, approve a code in the browser, done. Tokens refresh
  automatically; the 14‑day refresh token is rotated on every refresh.
- **REST polling** — the integration creates its own *telematics container(s)*
  covering the descriptors you selected, and polls
  `GET /customers/vehicles/{vin}/telematicData` on an interval. BMW allows
  **50 REST requests per 24 h**; the integration tracks this and stops a few
  requests short. Service/inspection data is *poll‑only* (BMW does not stream it),
  which is why container polling is the core of this integration.
- **MQTT streaming** — near‑real‑time updates for descriptors you tick in the
  portal's stream configuration (doors, charging, location). Uses the OAuth
  `id_token` as the MQTT password and reconnects when it rotates (~hourly).

## Prerequisites

1. An EU‑market BMW with an active SIM and ConnectedDrive contract, mapped to your
   account as **PRIMARY** user.
2. In the [BMW CarData portal](https://bmw-cardata.bmwgroup.com/customer):
   - **Create a CarData Client** → copy the *Client ID*.
   - **Subscribe** that client to both *CarData API* and *CarData Streaming*.
   - (For live updates) open **Configure data stream** and tick the descriptors
     you want streamed.
3. Remove any other CarData integration / MQTT client first — **BMW allows only
   one stream connection per account** and two clients will fight over it.

## Installation

### HACS (custom repository)
1. HACS → ⋮ → *Custom repositories* → add `https://github.com/marcnl/bmw-cardata-ha`
   as an *Integration*.
2. Install **BMW CarData**, restart Home Assistant.
3. Settings → Devices & Services → *Add Integration* → **BMW CarData**.

### Manual
Copy `custom_components/bmw_cardata` into your `config/custom_components/` and
restart.

## Options

Settings → Devices & Services → BMW CarData → *Configure*:

| Option | Default | Notes |
|---|---|---|
| Descriptor groups | Service & inspection, Core status | Add EV / Climate / Trip / Everything |
| REST poll interval | 2400 s (40 min) | Floor 1200 s; kept under the 50/day quota |
| Enable MQTT streaming | on | |
| Fetch charging history daily | off | 1 extra API call/vehicle/day |
| Fetch tyre diagnosis daily | off | 1 extra API call/vehicle/day |

Changing descriptor groups rebuilds the telematics container on reload.

## Services

- `bmw_cardata.poll_now` — immediate REST poll (uses quota)
- `bmw_cardata.refresh_tokens` — force a token refresh
- `bmw_cardata.recreate_container` — delete & recreate the container(s)

## Troubleshooting

Enable debug logging:

```yaml
logger:
  logs:
    custom_components.bmw_cardata: debug
```

- **No service entities** — confirm the container was created (log line
  “Created N CarData container(s)”) and that the first poll returned data. Some
  keys only populate after the car next uploads (drive/park cycle).
- **Stream keeps reconnecting** — another CarData client is connected with the
  same account.
- **Reauth prompt** — the refresh token expired (integration offline > 14 days)
  or the client was de‑subscribed in the portal.

## Disclaimer

Not affiliated with or endorsed by BMW. Uses the public BMW CarData customer API.
