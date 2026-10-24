# Configuration

Copy `examples/settings.json` to `config/settings.json`. The application never requires a speaker IP. It learns the address from discovery replies and follows the same device identity after an address change.

## Settings

| Key | Default | Meaning |
| --- | --- | --- |
| `bind` | `0.0.0.0` | IPv4 address on which the HTTP server listens. |
| `port` | `18730` | HTTP port. |
| `preferred_uuid` | `""` | Optional preferred discovered device identity. Empty selects a discovered speaker and keeps that choice while it remains available. |
| `interfaces` | `[]` | Optional Linux interface names, such as `["eth0"]`. Empty automatically examines suitable broadcast-capable interfaces. |
| `broadcast_targets` | `[]` | Optional broadcast destinations for unusual networks. Empty derives them from the host; do not put a speaker IP here. |
| `model_filter` | `S300` | Case-insensitive device-name filter for discovery replies. |
| `discovery_port` | `6000` | UDP discovery destination port. |
| `discovery_interval` | `30` | Seconds between discovery rounds. |
| `discovery_timeout` | `1.5` | Seconds spent collecting replies in a round. |
| `device_ttl` | `100` | Seconds a discovered device stays in the discovery list without a fresh reply. A working TCP connection can remain connected longer. |
| `poll_interval` | `4` | Seconds between background device queries when no setting is being applied. |
| `connect_timeout` | `3` | TCP connect and query timeout, in seconds. |

The browser polls backend state every two seconds while visible and pauses polling when hidden. All browsers share the backend's speaker connection.

## Environment overrides

| Variable | Meaning |
| --- | --- |
| `S300_CONFIG` | Path to the JSON settings file. Defaults to `./config/settings.json` relative to the current working directory. The Docker image uses `/config/settings.json`. |
| `S300_BIND` | Overrides `bind`. |
| `S300_PORT` | Overrides `port`. |
| `S300_PREFERRED_UUID` | Overrides `preferred_uuid`. |
| `S300_URL` | Default backend URL for the CLI. `--url` takes precedence. |

For the bundled Compose file, `S300_UID` and `S300_GID` set the container user. To add application overrides, use the service's `environment` block or a local Compose override file.

## Saved curves

`presets.json` is created next to `settings.json` the first time a curve is saved. It stores up to 24 custom curves. Writes use a temporary file and atomic replacement; an unsuccessful write does not replace the in-memory library.

The container user needs write access to this directory. Runtime configuration and curves are excluded from Git and Docker build context. Back them up independently from the source checkout.

## Multiple interfaces and devices

Automatic discovery skips common loopback, Docker, bridge, Tailscale, tunnel, and WireGuard interface names. It sends a limited broadcast and the directed broadcasts found on suitable interfaces. A non-Linux host can fall back to limited broadcast, but Linux host networking is the supported Docker deployment.

If discovery reaches more than one speaker, the UI shows a device selector. Selection does not apply stored settings to the new device. For a stable preferred choice after a service restart, set `preferred_uuid` using an identity learned through `/api/state`.

No IP access policy is part of these settings. Configure access in the reverse proxy.
