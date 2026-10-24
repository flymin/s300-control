# Edifier S300 LAN protocol

These notes describe observations on an Edifier S300 with firmware 01.13.07. They are not an official specification.

## Transport and discovery

| Transport | Purpose |
| --- | --- |
| UDP 6000 | Plaintext discovery request and reply. |
| TCP 8080 | XOR-encoded JSON requests and framed replies / pushes. |
| TCP 7001 | AirPlay-related service, outside this project's control transport. |

Discovery request:

```json
{"cmd":"discover"}
```

Illustrative reply:

```json
{
  "firm": "EDF",
  "encryption": 1,
  "protocol": 1,
  "info": {
    "name": "EDIFIER S300",
    "uuid": 12345,
    "host": "192.0.2.42",
    "port": 8080,
    "wifiMac": "02:00:00:00:00:01"
  }
}
```

Use the UDP sender's address rather than trusting the advertised `host`. Use UUID, or a returned identity fallback, to correlate later discovery replies after DHCP changes the address.

## Framing

Client → speaker:

1. Serialize a compact UTF-8 JSON object.
2. XOR every byte with `0xA5`.
3. Send directly on TCP, without a prefix, newline or length field.

Speaker → client:

```text
KxZK | XOR(big-endian uint16 body length, 0xA5) | XOR(JSON body, 0xA5)
```

The four magic bytes are unencoded. Both length bytes and body bytes are encoded. TCP packet boundaries do not match message boundaries: buffer incomplete headers and bodies, handle multiple frames per read, and tolerate unsolicited pushes between replies.

The XOR operation is obfuscation, not authenticated encryption.

## Queries and pushes

```json
{"id":"1700000000001","payload":"status_query"}
```

IDs must be numeric strings and are returned in matching responses. Non-numeric IDs have been observed to receive no reply. Successful snapshots include `deviceInfo`, `player`, `soundEffect`, `inputSource`, and `supportedFeatures`.

Heartbeat pushes and periodic status broadcasts can arrive without a request. Match replies by ID, and ingest complete status frames independently of matching a pending query.

## Verified settings

Volume:

```json
{"id":"1700000000002","payload":"settings","player":{"volume":8}}
```

Read `minVolume` and `maxVolume` from the device status rather than inventing another scale.

DIY equalizer example, showing all six bands:

```json
{
  "id": "1700000000003",
  "payload": "settings",
  "soundEffect": {
    "soundIndex": 1,
    "selectedIndex": 4,
    "soundEffectDIY": {
      "diyName": "Custom",
      "diyID": "13245322",
      "diyData": [
        {"fPoint":{"value":62},"gain":{"value":5}},
        {"fPoint":{"value":250},"gain":{"value":0}},
        {"fPoint":{"value":1000},"gain":{"value":0}},
        {"fPoint":{"value":4000},"gain":{"value":0}},
        {"fPoint":{"value":8000},"gain":{"value":0}},
        {"fPoint":{"value":16000},"gain":{"value":0}}
      ]
    }
  }
}
```

Observed gain bounds are −30 to +30 in increments of 5. The frequency points are fixed. Q was reported as fixed at 7 and could be omitted in verified writes. Existing DIY name and ID are preserved when available; the shown DIY ID is a request-template fallback, not a device identity.

The `(soundIndex=1, selectedIndex=4)` combination has been verified for DIY writes. The complete mapping of official application sound modes remains unconfirmed. Physical gain-to-dB conversion also remains unconfirmed.

## Readback is the acceptance criterion

A sent TCP buffer or successful settings acknowledgement does not prove that the speaker applied the setting. Send a new `status_query`, then compare the returned volume or six gains and DIY mode with the requested values.

Keep writes serialized over one persistent connection. Unbounded reconnections can exhaust the speaker's connection slots. Idle network standby may make discovery or control temporarily unresponsive.

## Research basis and boundaries

The transport behavior was established through device testing and inspection of Edifier Home request-building and TCP code, including the `com.edifier.s1000a` SDK, `SoundEffectWithJson`, `TcpClient`, `MessageBuffer`, and `CommandManager.encryptionPayload`. The XOR key corresponds to decimal 165.

The official application's release information is available from [Edifier's application metadata endpoint](https://foreign-app.edifier.com/home/api/v1/apps?tag=EdifierHome).

The device advertises additional settings, including factory reset and firmware operations. This project exposes only the independently verified EQ and volume controls.
