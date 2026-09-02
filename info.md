# BMW CarData

Home Assistant integration for BMW's official **CarData** platform (the
replacement for the retired ConnectedDrive feed).

- Device Code Flow login with just a Client ID from the BMW CarData portal
- Requests the full **service & inspection** descriptor set by default
  (Next Service, Next Inspection, Condition Based Services, teleservice history)
  plus core status (mileage, doors, windows, locks, tyres, location)
- REST polling within BMW's 50-requests/24h quota **plus** optional MQTT streaming
- Entities are created for whatever your specific vehicle reports

See the [README](https://github.com/marcnl/bmw-cardata-ha) for setup.
