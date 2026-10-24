"""UDP discovery on this host's LANs, without an IP/subnet baked into source."""
import ipaddress
import json
import socket
import struct
import time


def broadcast_targets(interfaces=None):
    targets = {"255.255.255.255"}
    try:
        import fcntl
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            for _, name in socket.if_nameindex():
                if interfaces and name not in interfaces:
                    continue
                if not interfaces and name.startswith(("lo", "docker", "veth", "br-", "tailscale", "tun", "wg")):
                    continue
                try:
                    packed = struct.pack("256s", name.encode()[:15])
                    flags = struct.unpack("H", fcntl.ioctl(probe, 0x8913, packed)[16:18])[0]
                    if flags & 1 and flags & 2:  # UP, BROADCAST
                        targets.add(socket.inet_ntoa(fcntl.ioctl(probe, 0x8919, packed)[20:24]))
                except OSError:
                    continue
    except (ImportError, OSError, AttributeError):
        pass
    return sorted(targets)


def parse_reply(data, address, model_filter="S300"):
    try:
        obj = json.loads(data)
        info = obj["info"]
        if obj.get("firm") != "EDF" or not isinstance(info, dict):
            return None
        if model_filter and model_filter.casefold() not in str(info.get("name", "")).casefold():
            return None
        host = str(ipaddress.IPv4Address(address[0]))  # Trust sender, not advertised host.
        port = int(info.get("port", 8080))
        identity = str(info.get("uuid") or info.get("wifiMac") or "")
        if not identity or not 1 <= port <= 65535:
            return None
        return {"id": identity, "name": str(info.get("name", "EDIFIER S300"))[:80],
                "host": host, "port": port, "mac": str(info.get("wifiMac", "")),
                "discovered_at": time.time()}
    except (ValueError, TypeError, KeyError):
        return None


def discover(config):
    targets = config.get("broadcast_targets") or broadcast_targets(config.get("interfaces"))
    result = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("0.0.0.0", 0))
        for target in targets:
            try:
                sock.sendto(b'{"cmd":"discover"}', (target, config["discovery_port"]))
            except OSError:
                continue
        end = time.monotonic() + config["discovery_timeout"]
        while time.monotonic() < end:
            sock.settimeout(max(0.01, end - time.monotonic()))
            try:
                data, address = sock.recvfrom(16384)
            except socket.timeout:
                break
            device = parse_reply(data, address, config.get("model_filter", "S300"))
            if device:
                result[device["id"]] = device
    return result
