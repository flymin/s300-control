"""Edifier LAN protocol. One connection per backend, numeric request IDs only."""
import json
import socket
import threading
import time

MAGIC = b"KxZK"
BANDS = (62, 250, 1000, 4000, 8000, 16000)
SOUND_MODES = (
    {"id": 0, "name": "经典", "description": "Classic"},
    {"id": 1, "name": "监听", "description": "Monitor"},
    {"id": 2, "name": "动感", "description": "Dynamic"},
    {"id": 3, "name": "人声", "description": "Vocal"},
    {"id": 4, "name": "DIY", "description": "自定义 EQ"},
)
_id_lock = threading.Lock()
_last_id = 0


def new_id():
    global _last_id
    with _id_lock:
        _last_id = max(int(time.time() * 1000), _last_id + 1)
        return str(_last_id)


def xor(data):
    return bytes(b ^ 0xA5 for b in data)


def encode(obj):
    return xor(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode())


class FrameParser:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        frames = []
        while True:
            pos = self.buffer.find(MAGIC)
            if pos < 0:
                self.buffer[:] = self.buffer[-3:]
                break
            if pos:
                del self.buffer[:pos]
            if len(self.buffer) < 6:
                break
            length = int.from_bytes(xor(self.buffer[4:6]), "big")
            if length == 0:
                del self.buffer[:6]
                continue
            if len(self.buffer) < 6 + length:
                break
            body = bytes(self.buffer[6:6 + length])
            del self.buffer[:6 + length]
            try:
                obj = json.loads(xor(body).decode())
                if isinstance(obj, dict):
                    frames.append(obj)
            except (ValueError, UnicodeError):
                pass
        return frames


class DeviceError(Exception):
    pass


class SpeakerConnection:
    def __init__(self, device, timeout=3.0, on_status=None):
        self.socket = socket.create_connection((device["host"], device["port"]), timeout)
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.parser = FrameParser()
        self.timeout = timeout
        self.on_status = on_status or (lambda x: None)
        self.latest_status = None

    def close(self):
        self.socket.close()

    def _receive(self, request_id, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.socket.settimeout(max(0.01, end - time.monotonic()))
            try:
                data = self.socket.recv(65536)
            except socket.timeout:
                break
            if not data:
                raise DeviceError("音箱断开了连接")
            match = None
            for obj in self.parser.feed(data):
                if all(k in obj for k in ("player", "soundEffect", "deviceInfo")):
                    self.latest_status = obj
                    self.on_status(obj)
                if str(obj.get("id")) == request_id:
                    match = obj
            if match is not None:
                if match.get("code", 0) != 0:
                    raise DeviceError("音箱拒绝了这次设置")
                return match
        return None

    def request(self, payload, timeout=None, **fields):
        request_id = new_id()
        self.socket.sendall(encode({"id": request_id, "payload": payload, **fields}))
        return self._receive(request_id, self.timeout if timeout is None else timeout)

    def query(self):
        result = self.request("status_query")
        if not result or not all(k in result for k in ("player", "soundEffect", "deviceInfo")):
            raise DeviceError("音箱未响应，可能正在待机")
        return self.latest_status

    def setting(self, fields, matches):
        # A settings acknowledgement is insufficient: verify actual device state.
        self.request("settings", timeout=0.35, **fields)
        time.sleep(0.06)
        for attempt in range(3):
            state = self.query()
            if matches(state):
                return state
            time.sleep(0.12 * (attempt + 1))
        raise DeviceError("音箱回读与设置不一致，请重试")


def gains_from(state):
    data = state.get("soundEffect", {}).get("soundEffectDIY", {}).get("diyData", [])
    values = {d.get("fPoint", {}).get("value"): d.get("gain", {}).get("value") for d in data}
    gains = [values.get(f) for f in BANDS]
    return gains if all(type(g) is int and -30 <= g <= 30 for g in gains) else None


def mode_from(state):
    effect = state.get("soundEffect", {})
    index = effect.get("selectedIndex")
    return index if effect.get("soundIndex") == 1 and type(index) is int and 0 <= index <= 4 else None


def mode_fields(index):
    return {"soundEffect": {"soundIndex": 1, "selectedIndex": index}}


def eq_fields(gains, state):
    current = state.get("soundEffect", {}).get("soundEffectDIY", {})
    return {"soundEffect": {
        "soundIndex": 1, "selectedIndex": 4,
        "soundEffectDIY": {
            "diyName": current.get("diyName", "Custom"),
            "diyID": current.get("diyID", "13245322"),
            "diyData": [{"fPoint": {"value": f}, "gain": {"value": g}}
                        for f, g in zip(BANDS, gains)],
        },
    }}
