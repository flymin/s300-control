# HTTP API

All paths are relative to the backend root. JSON writes require `Content-Type: application/json`. The browser uses same-origin requests. IP restrictions and authentication are delegated to the reverse proxy.

| Method | Path | Body / result |
| --- | --- | --- |
| GET | `/healthz` | `ok` and `speaker_connected`. Service health is separate from device availability. |
| GET | `/api/state` | Connection, discovered devices, current device state, backend `instance_id`, revision, last-seen timestamp, bands and presets. |
| POST | `/api/discover` | `{}` schedules an immediate discovery round. The response is the current snapshot, not a guarantee that discovery has completed. |
| POST | `/api/device` | `{"id":"demo"}` selects an identity already present in the discovery list. |
| POST | `/api/eq` | `{"gains":[0,5,0,0,-5,0]}`. Six integers, each −30…30 and divisible by 5. |
| POST | `/api/volume` | `{"volume":8}`. Integer within the device-reported range. |
| POST | `/api/presets` | `{"name":"Evening","gains":[0,5,0,0,-5,0]}`. Saves a custom curve; does not apply it to the speaker. |
| POST | `/api/presets/delete` | `{"id":"example-curve-id"}`. Deletes a saved custom curve; does not change the current speaker EQ. |

Successful writes return a complete snapshot. Errors return an `error` string with an appropriate HTTP status. Invalid values produce 400; unsupported content types produce 415; oversized requests produce 413; disconnected speakers or failed readback produce 503.

`instance_id` changes when the backend restarts. Revisions order snapshots within one instance; compare revisions only when the instance IDs match.

EQ and volume endpoints return success only after a new device query confirms the requested setting. A settings acknowledgement alone does not establish success. Requests rejected while offline are not saved for later replay.

The API's gain values are raw device units. EQ writes enable the experimentally verified DIY mode. Factory modes and dangerous device commands are not API endpoints.

## CLI

```sh
s300 --url http://localhost:18730/
s300 --url http://localhost:18730/ --eq 5 0 0 0 0 0
s300 --url http://localhost:18730/ --volume 8
```

Set `S300_URL` to avoid repeating the URL. The CLI deliberately bypasses proxy environment variables for its requests. It uses the backend rather than creating another speaker connection.
