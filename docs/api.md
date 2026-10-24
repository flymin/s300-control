# HTTP API

All paths are relative to the backend root. JSON writes require `Content-Type: application/json`. The browser uses same-origin requests. IP restrictions and authentication are delegated to the reverse proxy.

| Method | Path | Body / result |
| --- | --- | --- |
| GET | `/healthz` | `ok` and `speaker_connected`. Service health is separate from device availability. |
| GET | `/api/state` | Connection, discovered devices, current device state, backend `instance_id`, `connection_epoch`, revision, last-seen timestamp, sound modes, bands and presets. |
| POST | `/api/discover` | `{}` schedules an immediate discovery round. The response is the current snapshot, not a guarantee that discovery has completed. |
| POST | `/api/device` | `{"id":"demo"}` selects an identity already present in the discovery list. |
| POST | `/api/mode` | `{"selected_index":1}`. Selects mode 0…4 without changing the stored DIY curve. |
| POST | `/api/eq` | `{"gains":[0,5,0,0,-5,0]}`. Six integers, each −30…30 and divisible by 5. Requires DIY mode. |
| POST | `/api/volume` | `{"volume":8}`. Integer within the device-reported range. |
| POST | `/api/presets` | `{"name":"Evening","gains":[0,5,0,0,-5,0]}`. Saves a custom curve; does not apply it to the speaker. |
| POST | `/api/presets/delete` | `{"id":"example-curve-id"}`. Deletes a saved custom curve; does not change the current speaker EQ. |

Successful writes return a complete snapshot. Errors return an `error` string with an appropriate HTTP status. Invalid values produce 400; EQ requests outside DIY mode or queued commands whose connection changed produce 409; unsupported content types produce 415; oversized requests produce 413; disconnected speakers or failed readback produce 503.

Custom curve names are trimmed and must contain 1–24 characters. Up to 24 custom curves can be stored. Saving and deleting curves only changes the library; applying a curve uses `/api/eq` and therefore requires DIY mode and a live speaker connection.

`instance_id` changes when the backend restarts. Revisions order snapshots within one instance; compare revisions only when the instance IDs match.

`connection_epoch` changes when the device connection is invalidated, including reconnects to the same device. Browser control requests include a `context` object with `instance_id`, `connection_epoch`, and `device_id` from the displayed snapshot. A mismatch returns 409, so an old page cannot submit a pending adjustment to a new connection. Direct API and CLI callers may omit `context` to target the current connection; queued operations remain bound to the connection on which they were submitted.

Mode, EQ, and volume endpoints return success only after a new device query confirms the requested setting. A settings acknowledgement alone does not establish success. Requests rejected while offline are not saved for later replay.

The snapshot's `sound_modes` lists mode IDs and display labels. `state.selected_index` is 0=Classic, 1=Monitor, 2=Dynamic, 3=Vocal, or 4=DIY; it is `null` if the reported mode is unrecognized. `state.gains` describes the stored DIY curve even when a factory mode is selected, and `state.eq_active` indicates whether that curve is selected.

The API's gain values are raw device units. Select DIY through `/api/mode` before editing EQ. The backend rechecks the speaker's mode before each EQ write and rejects the operation if another client has selected a factory mode. Mode writes contain only `soundIndex:1` and `selectedIndex`, preserving DIY data. Firmware operations and factory reset are not exposed.

## CLI

```sh
s300 --url http://localhost:18730/
s300 --url http://localhost:18730/ --mode 1
s300 --url http://localhost:18730/ --mode 4 --eq 5 0 0 0 0 0
s300 --url http://localhost:18730/ --volume 8
```

Set `S300_URL` to avoid repeating the URL. The CLI deliberately bypasses proxy environment variables for its requests. It uses the backend rather than creating another speaker connection.
