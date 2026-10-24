<div align="center">

<img src="docs/assets/hero.svg" alt="S300 Control — fine-tune your everyday listening" width="100%">

**A small, self-hosted control room for your Edifier S300.**

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-e6b450?style=flat-square)](LICENSE)
[![PWA](https://img.shields.io/badge/PWA-Installable-687586?style=flat-square)](#install-on-your-phone)
[![Runtime dependencies](https://img.shields.io/badge/Runtime_dependencies-0-424b59?style=flat-square)](pyproject.toml)

[Quick start](#quick-start) · [Screenshots](#a-familiar-place-for-your-sound) · [Configuration](docs/configuration.md) · [Protocol](docs/protocol.md)

English · [简体中文](README.zh-CN.md)

</div>

---

S300 Control brings the speaker's sound modes, DIY equalizer, and volume to a compact web interface. Choose a factory mode, or select DIY to adjust six bands and save your own curves. The backend discovers speakers on its own LAN and verifies every setting by reading it back from the device.

**This is an independent, unofficial project** and is not affiliated with, endorsed by, or sponsored by Edifier. Product names are used solely to identify compatible devices.

There is no frontend build step, account, cloud relay, or runtime package dependency. The UI is currently in Simplified Chinese; both documentation languages cover the same setup.

## A familiar place for your sound

<img src="docs/assets/desktop-black.jpg" alt="Black palette: speaker and volume cards above sound modes, with the six-band equalizer and saved curves expanded in DIY mode" width="100%">

*Black palette (Ayu Dark) · DIY mode.*

<details>
<summary><strong>See the mobile layout</strong></summary>
<br>
<img src="docs/assets/mobile-black.jpg" alt="The same black palette and DIY controls at phone width" width="320">
</details>

## What you get

| | |
| :--- | :--- |
| **Sound modes first** | Classic, Monitor, Dynamic, Vocal, and DIY. The interface shows EQ controls only in DIY mode. |
| **Six DIY bands, one clear view** | 62 Hz to 16 kHz, with drag, step buttons, keyboard controls, and a live curve. |
| **Quick curves and your own collection** | Six built-in DIY curves; save custom curves and share them across your browsers through the backend. |
| **Finds its own speaker** | UDP discovery, stable device identity, and automatic reconnection when an address changes. |
| **Settings you can trust** | One persistent TCP connection, serialized writes, and a fresh device readback after each setting. |
| **Fits a phone** | Responsive layout, safe-area support, an installable PWA, and lossless app icons up to 1024 px. |
| **Easy to host** | Python standard library at runtime, Docker host networking, a read-only image, and a small configuration directory. |

The speaker supports gains from **−30 to +30 in steps of 5**. These are device gain units; the physical dB conversion has not been established. Quick curves are this project's DIY combinations, not official factory sound modes.

Switching sound modes preserves the stored DIY curve. To edit it, select **DIY** first; factory modes hide the sliders, quick curves, and saved-curve library. Device details, volume, and mode selection stay in place while DIY controls expand below.

## Quick start

Use a **Linux Docker host on the speaker's LAN**. Host networking lets the backend receive LAN discovery replies. No speaker IP is required.

From a checkout of this repository:

```sh
mkdir -p config
cp examples/settings.json config/settings.json
docker compose -f devlop/compose.yaml up -d --build
```

Open **`http://<your-server>:18730/`**. The backend will discover the speaker automatically. Check progress with:

```sh
docker compose -f devlop/compose.yaml ps
docker compose -f devlop/compose.yaml logs --tail=50 s300
```

The container runs as UID/GID 1000 by default. If your checkout belongs to another user, set `S300_UID` and `S300_GID` to that user's IDs when starting Compose. The `config/` directory must be writable by the container user.

Configuration and saved curves stay in `config/`. See [configuration](docs/configuration.md) for available settings and [deployment](docs/deployment.md) for reverse proxy setup and backups.

Set `theme` in `config/settings.json` to `black`, `white`, or `coffee` to match your speaker, then restart the service. Each palette stays consistent throughout the day; there is no browser theme switch.

### Run with Python

Python 3.11+ is sufficient for the backend. Linux is recommended for automatic interface discovery.

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
mkdir -p config
cp examples/settings.json config/settings.json
python -m s300_control
```

The installed command `s300-control` starts the same server. `s300 --url http://localhost:18730/` queries it; the CLI shares the backend's discovery and device connection.

## Install on your phone

Serve the app through **HTTPS** with your existing reverse proxy. On iPhone, open the HTTPS URL in Safari, choose **Share → Add to Home Screen**, and enable **Open as Web App** if offered. On browsers that support an installation prompt, the app's install button opens it.

On iPhone, the page supplies a single opaque 1024×1024 PNG matching the configured palette for the home-screen icon. Other browsers use the matching multi-resolution and maskable icons in the manifest.

The Service Worker caches the interface. Device state and control requests are always live: no cached status and no replay of changes after an outage. An offline interface cannot control an unreachable speaker.

See [PWA and HTTPS notes](docs/deployment.md#pwa-and-app-icons), including refreshing an already-installed iOS icon.

## How it fits together

```mermaid
flowchart LR
  UI[Browser / PWA] -->|Same-origin HTTP| API[Python backend]
  API -->|UDP discovery| LAN[Speaker's LAN]
  API <-->|One TCP connection| S300[Edifier S300]
  API -->|Saved curves| Config[config/]
```

The browser never connects directly to a speaker IP. The backend observes the speaker's LAN, matches replies to device identities, and owns the TCP connection. EQ drags update the interface immediately; only the latest pending value is sent while a transaction is in flight.

The service has no built-in authentication. Restrict access to trusted clients through your network or reverse proxy.

## Project map

```text
src/s300_control/   Backend package and bundled PWA
tests/             Protocol, simulated TCP device, API, and install-asset tests
docs/              Configuration, deployment, HTTP API, and protocol notes
examples/          Generic configuration to copy into your own config/
tools/             Synthetic UI preview and icon generation
devlop/            Dockerfile, Compose, and Docker build ignore rules
```

## Development

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
node --test tests/test_frontend.cjs
node --check src/s300_control/static/app.js
node --check src/s300_control/static/sw.js
```

No physical speaker is needed for these tests. They cover fragmented frames, pushed heartbeats, ignored settings despite acknowledgements, device readback, address changes, request validation, persistent curves, app-icon dimensions, and frontend recovery after disconnects or backend restarts.

To explore the UI without a speaker:

```sh
python tools/preview.py --theme black --mode 4
```

Open `http://127.0.0.1:18731/`. This preview uses synthetic data and does not start network discovery or a speaker connection. Its saved curves are temporary. `--theme` accepts `black`, `white`, or `coffee`; `--mode` accepts 0–4, and `--port` changes the preview listener.

To regenerate the artwork:

```sh
python -m pip install -e '.[artwork]'
python tools/generate_icons.py
```

## Compatibility and scope

- Protocol behavior has been verified on Edifier S300 firmware **01.13.07**. Other firmware and related Edifier models need independent verification.
- Implemented controls: five sound modes, six-band EQ within DIY mode, and volume, including mute through volume zero.
- Unverified input switching, playback commands, shutdown, and factory reset are not exposed.
- A sleeping speaker or its limited connection slots can delay reconnection. Wake it and close unused control clients before retrying.

[Protocol notes](docs/protocol.md) document what was observed and what remains uncertain.

## Feedback

Bug reports and verified protocol findings are welcome. Include the firmware version, steps to reproduce, and expected behavior.

## License

[MIT](LICENSE). Edifier and S300 are trademarks of their respective owners.
