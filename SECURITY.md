# Security model

S300 Control is a LAN device controller. It contains no login system, IP access policy, or cloud relay. Deploy it behind a reverse proxy when access restrictions or authentication are required. The speaker's XOR transport does not provide authenticated encryption.

The HTTP backend validates request shapes and setting ranges, rejects cross-origin browser writes, and verifies settings through device readback. These checks do not replace network access restrictions.

Runtime `config/`, environment files, credentials and raw device captures are not part of this repository. State endpoints may return network names and device identities; treat exported diagnostics accordingly.

## Reporting a vulnerability

Use the repository host's private vulnerability-reporting feature when it is enabled. If no private channel is available, ask the maintainers for a private reporting channel without publishing exploit details, credentials or personal device data in the initial public message.
