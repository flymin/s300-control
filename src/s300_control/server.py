#!/usr/bin/env python3
"""HTTP API, preset storage, and speaker connection management."""
import copy
import json
import logging
import mimetypes
import os
from pathlib import Path
import queue
import signal
import threading
import time
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
import uuid

from .discovery import discover
from .protocol import BANDS, SOUND_MODES, DeviceError, SpeakerConnection, eq_fields, gains_from, mode_fields, mode_from

LOG = logging.getLogger("s300")
ROOT = Path(__file__).resolve().parent
DEFAULTS = {"bind": "0.0.0.0", "port": 18730, "discovery_port": 6000,
            "discovery_timeout": 1.5, "discovery_interval": 30, "device_ttl": 100,
            "connect_timeout": 3, "poll_interval": 4, "preferred_uuid": "",
            "interfaces": [], "broadcast_targets": [], "model_filter": "S300", "theme": "black"}
THEME_COLORS = {"black": "#0b0e14", "white": "#e8edf2", "coffee": "#282828"}
BUILTINS = [
    {"id": "flat", "name": "原声", "description": "平直自然", "gains": [0, 0, 0, 0, 0, 0]},
    {"id": "bass", "name": "低音", "description": "更有分量", "gains": [10, 5, 0, 0, 0, 0]},
    {"id": "voice", "name": "人声", "description": "清晰靠前", "gains": [-5, 0, 10, 5, 0, -5]},
    {"id": "warm", "name": "温暖", "description": "柔和耐听", "gains": [5, 10, 0, -5, -5, 0]},
    {"id": "clear", "name": "明亮", "description": "细节通透", "gains": [0, 0, 0, 5, 10, 5]},
    {"id": "night", "name": "夜间", "description": "收敛低频", "gains": [-10, -5, 5, 5, 0, -5]},
]


class ControlConflict(DeviceError):
    pass


class ModeConflict(ControlConflict):
    pass


def validate_mode(index):
    if type(index) is not int or not 0 <= index <= 4:
        raise ValueError("音效模式需要 0～4 的整数")
    return index


def validate_gains(gains):
    if not isinstance(gains, list) or len(gains) != 6 or any(
            type(g) is not int or not -30 <= g <= 30 or g % 5 for g in gains):
        raise ValueError("EQ 需要六个 −30～+30、步进为 5 的整数")
    return gains[:]


def load_config(path):
    config = dict(DEFAULTS)
    if path.exists():
        config.update(json.loads(path.read_text(encoding="utf-8")))
    for key in ("bind", "port", "preferred_uuid"):
        value = os.getenv("S300_" + key.upper())
        if value is not None:
            config[key] = int(value) if key == "port" else value
    if not 1 <= int(config["port"]) <= 65535:
        raise ValueError("Invalid HTTP port")
    for key in ("discovery_timeout", "discovery_interval", "device_ttl", "connect_timeout", "poll_interval"):
        if not isinstance(config[key], (int, float)) or config[key] <= 0:
            raise ValueError("Invalid configuration: " + key)
    if not isinstance(config["theme"], str) or config["theme"] not in THEME_COLORS:
        raise ValueError("theme must be black, white, or coffee")
    return config


class Presets:
    def __init__(self, path):
        self.path, self.lock, self.custom = path, threading.RLock(), []
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError("presets.json must be an array")
            for item in data:
                validate_gains(item["gains"])
            self.custom = data

    def list(self):
        with self.lock:
            return copy.deepcopy([dict(p, custom=False) for p in BUILTINS] + self.custom)

    def save(self, body):
        gains = validate_gains(body.get("gains"))
        name = body.get("name", "")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 24:
            raise ValueError("请填写 1～24 字的曲线名称")
        with self.lock:
            if len(self.custom) >= 24:
                raise ValueError("最多保存 24 条曲线，请先删除一条")
            entry = {"id": uuid.uuid4().hex, "name": name.strip(), "description": "我的曲线",
                     "gains": gains, "custom": True}
            self._write(self.custom + [entry])
            return entry

    def delete(self, identity):
        with self.lock:
            remaining = [p for p in self.custom if p["id"] != identity]
            if len(remaining) == len(self.custom):
                raise ValueError("找不到这条自定义曲线")
            self._write(remaining)

    def _write(self, entries):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, self.path)
        self.custom = entries


class Controller:
    def __init__(self, config, presets):
        self.config, self.presets = config, presets
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.rescan = threading.Event()
        self.jobs = queue.Queue(maxsize=16)
        self.devices, self.device, self.raw = {}, None, None
        self.selected = str(config.get("preferred_uuid", ""))
        self.status, self.error = "discovering", "正在寻找局域网中的 S300"
        self.instance_id = uuid.uuid4().hex
        self.revision, self.last_seen = 0, None
        self.connection = None
        self.connection_epoch = 0
        self.threads = []

    def start(self):
        for target in (self._discovery_loop, self._control_loop):
            thread = threading.Thread(target=target, daemon=True)
            self.threads.append(thread)
            thread.start()

    def _discovery_loop(self):
        while not self.stop.is_set():
            self.rescan.clear()
            try:
                found = discover(self.config)
                with self.lock:
                    self.devices.update(found)
                    cutoff = time.time() - self.config["device_ttl"]
                    self.devices = {k: v for k, v in self.devices.items() if v["discovered_at"] >= cutoff}
                    self.revision += 1
            except OSError as exc:
                LOG.warning("Discovery: %s", exc)
            self.rescan.wait(self.config["discovery_interval"])

    def _ingest(self, state):
        with self.lock:
            self.raw = state
            self.status, self.error = "online", ""
            self.last_seen = time.time()
            self.revision += 1

    def snapshot(self):
        with self.lock:
            raw = self.raw or {}
            player, info, effect = raw.get("player", {}), raw.get("deviceInfo", {}), raw.get("soundEffect", {})
            state = None if self.raw is None else {
                "volume": player.get("volume"), "max_volume": player.get("maxVolume", 30),
                "min_volume": player.get("minVolume", 0), "gains": gains_from(raw),
                "sound_index": effect.get("soundIndex"), "selected_index": mode_from(raw),
                "eq_active": mode_from(raw) == 4,
                "firmware": info.get("firmwareVersion", ""), "wifi": info.get("wifiName", ""),
                "name": info.get("airplayName", "EDIFIER S300"),
            }
            return {"connection": self.status, "error": self.error,
                    "connection_epoch": self.connection_epoch,
                    "device": copy.deepcopy(self.device), "devices": copy.deepcopy(list(self.devices.values())),
                    "state": state, "instance_id": self.instance_id,
                    "revision": self.revision, "last_seen": self.last_seen,
                    "bands": list(BANDS), "sound_modes": copy.deepcopy(list(SOUND_MODES)),
                    "presets": self.presets.list()}

    def _choose(self):
        with self.lock:
            if self.selected:
                return copy.deepcopy(self.devices.get(self.selected))
            if self.device and self.device["id"] in self.devices:
                return copy.deepcopy(self.devices[self.device["id"]])
            return copy.deepcopy(next(iter(sorted(self.devices.values(), key=lambda d: d["id"])), None))

    def _disconnect(self, error):
        with self.lock:
            connection, self.connection = self.connection, None
            self.connection_epoch += 1
            self.status, self.error = "offline", error
            self.revision += 1
        if connection:
            connection.close()

    def submit(self, kind, body):
        with self.lock:
            if self.status != "online" or not self.connection:
                raise DeviceError("音箱尚未连接，请等待自动发现或重新搜索")
            target = self.device["id"]
            epoch = self.connection_epoch
            if "context" in body:
                context = body["context"]
                if not isinstance(context, dict) or not isinstance(context.get("instance_id"), str) or (
                        type(context.get("connection_epoch")) is not int) or not isinstance(context.get("device_id"), str):
                    raise ValueError("控制上下文格式错误")
                if context != {"instance_id": self.instance_id, "connection_epoch": epoch, "device_id": target}:
                    raise ControlConflict("音箱连接已变化，请刷新状态后重试")
        future = Future()
        try:
            self.jobs.put_nowait((kind, body, future, time.monotonic() + 10, target, epoch))
        except queue.Full:
            raise DeviceError("操作较多，请稍后重试")
        return future.result(timeout=12)

    def select(self, identity):
        with self.lock:
            if identity not in self.devices:
                raise ValueError("请先搜索这台音箱")
            self.selected = identity
            self.revision += 1

    def _execute(self, kind, body):
        if kind == "mode":
            index = body["selected_index"]
            return self.connection.setting(mode_fields(index), lambda s: mode_from(s) == index)
        if kind == "eq":
            current = self.connection.query()
            self._ingest(current)
            if mode_from(current) != 4:
                raise ModeConflict("请先切换到 DIY 模式后再调整 EQ")
            gains = body["gains"]
            return self.connection.setting(eq_fields(gains, self.raw), lambda s:
                gains_from(s) == gains and s["soundEffect"].get("soundIndex") == 1
                and s["soundEffect"].get("selectedIndex") == 4)
        volume = body["volume"]
        return self.connection.setting({"player": {"volume": volume}},
                                       lambda s: s.get("player", {}).get("volume") == volume)

    def _control_loop(self):
        next_poll, next_connect = 0, 0
        while not self.stop.is_set():
            candidate = self._choose()
            if self.connection and candidate and (candidate["id"], candidate["host"], candidate["port"]) != (
                    self.device["id"], self.device["host"], self.device["port"]):
                self._disconnect("正在连接所选音箱")
                with self.lock:
                    self.raw = None
                    self.revision += 1
                next_connect = 0
            if not self.connection and candidate and time.monotonic() >= next_connect:
                with self.lock:
                    if self.device != candidate:
                        self.raw = None
                    self.device = candidate
                    self.status, self.error = "connecting", "正在连接音箱"
                    self.revision += 1
                try:
                    self.connection = SpeakerConnection(candidate, self.config["connect_timeout"], self._ingest)
                    self._ingest(self.connection.query())
                    next_poll = time.monotonic() + self.config["poll_interval"]
                except (OSError, DeviceError) as exc:
                    self._disconnect(str(exc))
                    self.rescan.set()
                    next_connect = time.monotonic() + 5
            try:
                kind, body, future, expiry, target, epoch = self.jobs.get(timeout=0.1)
            except queue.Empty:
                if self.connection and time.monotonic() >= next_poll:
                    try:
                        self._ingest(self.connection.query())
                    except (OSError, DeviceError) as exc:
                        self._disconnect(str(exc))
                        self.rescan.set()
                        next_connect = time.monotonic() + 5
                    next_poll = time.monotonic() + self.config["poll_interval"]
                continue
            try:
                if time.monotonic() > expiry or epoch != self.connection_epoch or not self.connection or target != self.device["id"] or (
                        self.selected and self.selected != target):
                    raise ControlConflict("连接已变化，这次设置未执行，请重试")
                self._ingest(self._execute(kind, body))
                future.set_result(self.snapshot())
                next_poll = time.monotonic() + self.config["poll_interval"]
            except ControlConflict as exc:
                future.set_exception(exc)
            except (OSError, DeviceError) as exc:
                self._disconnect(str(exc))
                next_connect = time.monotonic() + 5
                self.rescan.set()
                future.set_exception(DeviceError(str(exc)))
            except Exception as exc:
                LOG.exception("Control transaction failed")
                future.set_exception(DeviceError("设置失败，请重试"))
            finally:
                self.jobs.task_done()
        self._disconnect("服务已停止")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, fmt, *args):
        if args and str(args[0]).startswith("GET /api/state"):
            return
        LOG.info(fmt, *args)

    def respond(self, status, body, content_type="application/json; charset=utf-8", cache="no-store"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path == "/api/state":
            return self.respond(200, self.server.controller.snapshot())
        if path == "/healthz":
            return self.respond(200, {"ok": True, "speaker_connected": self.server.controller.status == "online"})
        if path.startswith("/api/"):
            return self.respond(404, {"error": "接口不存在"})
        if path == "/":
            path = "/index.html"
        static = ROOT / "static"
        file = (static / path.lstrip("/")).resolve()
        if not file.is_relative_to(static) or not file.is_file():
            return self.respond(404, {"error": "页面不存在"})
        ctype = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
        if file.name.endswith(".webmanifest"):
            ctype = "application/manifest+json"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        data = file.read_bytes()
        theme = self.server.controller.config["theme"]
        if file.name == "index.html":
            html = data.decode("utf-8").replace('data-theme="black"', 'data-theme="' + theme + '"', 1)
            html = html.replace('name="theme-color" content="#0b0e14"',
                                'name="theme-color" content="' + THEME_COLORS[theme] + '"', 1)
            html = html.replace('./icons/v4/black/', './icons/v4/' + theme + '/')
            if theme == "white":
                html = html.replace('content="black-translucent"', 'content="default"', 1)
            data = html.encode("utf-8")
        elif file.name == "manifest.webmanifest":
            manifest = json.loads(data)
            manifest["theme_color"] = manifest["background_color"] = THEME_COLORS[theme]
            for icon in manifest["icons"]:
                icon["src"] = icon["src"].replace('./icons/v4/black/', './icons/v4/' + theme + '/')
            data = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
        elif file.name == "sw.js":
            worker = data.decode("utf-8").replace('./icons/v4/black/', './icons/v4/' + theme + '/')
            worker = worker.replace('s300-shell-v10-black', 's300-shell-v10-' + theme)
            data = worker.encode("utf-8")
        self.respond(200, data, ctype, "no-cache")

    def do_POST(self):
        try:
            # Reject browser writes from a different origin.
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                return self.respond(403, {"error": "请在服务页面内操作"})
            if self.headers.get("Transfer-Encoding"):
                return self.respond(400, {"error": "不支持此请求格式"})
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 8192:
                self.close_connection = True
                return self.respond(413, {"error": "请求过大"})
            if self.headers.get_content_type() != "application/json":
                self.close_connection = True
                return self.respond(415, {"error": "需要 JSON 请求"})
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("请求格式错误")
            path = urlsplit(self.path).path
            controller = self.server.controller
            if path == "/api/eq":
                body["gains"] = validate_gains(body.get("gains"))
                return self.respond(200, controller.submit("eq", body))
            if path == "/api/mode":
                body["selected_index"] = validate_mode(body.get("selected_index"))
                return self.respond(200, controller.submit("mode", body))
            if path == "/api/volume":
                volume = body.get("volume")
                state = controller.snapshot()["state"] or {}
                if type(volume) is not int or not state.get("min_volume", 0) <= volume <= state.get("max_volume", 30):
                    raise ValueError("音量超出设备范围")
                return self.respond(200, controller.submit("volume", body))
            if path == "/api/discover":
                controller.rescan.set()
            elif path == "/api/device":
                controller.select(str(body.get("id", "")))
            elif path == "/api/presets":
                with controller.lock:
                    controller.presets.save(body)
                    controller.revision += 1
            elif path == "/api/presets/delete":
                with controller.lock:
                    controller.presets.delete(body.get("id"))
                    controller.revision += 1
            else:
                return self.respond(404, {"error": "接口不存在"})
            self.respond(200, controller.snapshot())
        except (ValueError, KeyError, TypeError) as exc:
            self.respond(400, {"error": str(exc)})
        except ControlConflict as exc:
            self.respond(409, {"error": str(exc)})
        except (DeviceError, TimeoutError) as exc:
            self.respond(503, {"error": str(exc) or "音箱响应超时，请重试"})
        except OSError:
            LOG.exception("Configuration write failed")
            self.respond(500, {"error": "保存失败，请检查 config 目录的写入权限"})


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    path = Path(os.getenv("S300_CONFIG", str(Path.cwd() / "config" / "settings.json")))
    config = load_config(path)
    controller = Controller(config, Presets(path.parent / "presets.json"))
    server = ThreadingHTTPServer((config["bind"], config["port"]), Handler)
    server.daemon_threads = True
    server.controller = controller
    controller.start()

    def shutdown(*_):
        controller.stop.set()
        controller.rescan.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    LOG.info("S300 HTTP service listening on %s:%s", config["bind"], config["port"])
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        shutdown()
        server.server_close()
        for thread in controller.threads:
            thread.join(timeout=config["connect_timeout"] + 1)


if __name__ == "__main__":
    main()
