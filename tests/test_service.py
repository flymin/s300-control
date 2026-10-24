import copy
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from s300_control.discovery import parse_reply
from s300_control.protocol import BANDS, DeviceError, FrameParser, SpeakerConnection, encode, eq_fields, gains_from, new_id, xor
from s300_control.server import Controller, DEFAULTS, Handler, Presets, ThreadingHTTPServer, validate_gains


def state():
    return {"deviceInfo": {"airplayName": "Test S300"},
            "player": {"volume": 6, "maxVolume": 30, "minVolume": 0},
            **eq_fields([0] * 6, {})}


def frame(obj):
    body = json.dumps(obj).encode()
    return b"KxZK" + xor(len(body).to_bytes(2, "big")) + xor(body)


class FakeSpeaker:
    """A real TCP peer: fragmented responses, pushed heartbeats and optional ignored writes."""
    def __init__(self, ignore=False):
        self.raw, self.ignore, self.writes, self.connections = state(), ignore, 0, 0
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.listener.accept()
            except socket.timeout:
                continue
            self.connections += 1
            with conn:
                conn.settimeout(0.1)
                buf = ""
                while not self.stop.is_set():
                    try:
                        data = conn.recv(8192)
                        if not data:
                            break
                    except socket.timeout:
                        continue
                    buf += xor(data).decode()
                    while buf:
                        try:
                            obj, end = json.JSONDecoder().raw_decode(buf)
                        except ValueError:
                            break
                        buf = buf[end:]
                        if obj["payload"] == "settings":
                            self.writes += 1
                            if not self.ignore:
                                for key in ("player", "soundEffect"):
                                    if key in obj:
                                        self.raw[key].update(obj[key])
                            reply = {"id": obj["id"], "code": 0, "payload": "settings"}
                        else:
                            reply = dict(copy.deepcopy(self.raw), id=obj["id"], code=0, payload="status_query")
                        output = frame({"payload": "heart_beat"}) + frame(reply)
                        try:
                            conn.sendall(output[:5]); conn.sendall(output[5:17]); conn.sendall(output[17:])
                        except (BrokenPipeError, ConnectionResetError):
                            break

    def close(self):
        self.stop.set()
        self.thread.join(2)
        self.listener.close()


class ProtocolTests(unittest.TestCase):
    def test_parser_every_fragment_boundary_and_push(self):
        obj = {"id": "123", "name": "音箱", "payload": "status_query"}
        stream = frame({"payload": "heart_beat"}) + frame(obj)
        for split in range(len(stream)):
            parser = FrameParser()
            result = parser.feed(b"noise" + stream[:split]) + parser.feed(stream[split:])
            self.assertEqual(result, [{"payload": "heart_beat"}, obj])

    def test_parser_bytewise_and_corrupt_json_recovery(self):
        stream = b"KxZK" + xor(b"\x00\x03bad") + frame({"code": 0})
        parser, result = FrameParser(), []
        for byte in stream:
            result.extend(parser.feed(bytes([byte])))
        self.assertEqual(result, [{"code": 0}])

    def test_numeric_ids_unique_even_with_fixed_clock(self):
        with patch('s300_control.protocol.time.time', return_value=1000):
            ids = [new_id() for _ in range(100)]
        self.assertEqual(len(set(ids)), 100)
        self.assertTrue(all(i.isdigit() for i in ids))

    def test_validation_and_reordered_frequency_readback(self):
        for value in (None, [0] * 5, [1] * 6, [35] * 6, [True] * 6, [0.0] * 6):
            with self.assertRaises(ValueError): validate_gains(value)
        self.assertEqual(validate_gains([-30, -5, 0, 5, 10, 30]), [-30, -5, 0, 5, 10, 30])
        s = state(); s['soundEffect']['soundEffectDIY']['diyData'].reverse()
        self.assertEqual(gains_from(s), [0] * 6)

    def test_real_socket_settings_are_read_back_and_connection_reused(self):
        speaker = FakeSpeaker()
        connection = SpeakerConnection({"host": "127.0.0.1", "port": speaker.port}, 1)
        try:
            connection.query()
            desired = [5, 0, -5, 10, 0, 0]
            result = connection.setting(eq_fields(desired, {}), lambda s: gains_from(s) == desired)
            self.assertEqual(gains_from(result), desired)
            result = connection.setting({"player": {"volume": 8}}, lambda s: s["player"]["volume"] == 8)
            self.assertEqual(result['player']['volume'], 8)
            self.assertEqual(speaker.connections, 1)
        finally:
            connection.close(); speaker.close()

    def test_acknowledged_but_ignored_write_fails_verification(self):
        speaker = FakeSpeaker(ignore=True)
        connection = SpeakerConnection({"host": "127.0.0.1", "port": speaker.port}, 1)
        try:
            with self.assertRaises(DeviceError):
                connection.setting({"player": {"volume": 8}}, lambda s: s['player']['volume'] == 8)
        finally:
            connection.close(); speaker.close()

    def test_discovery_uses_sender_and_stable_identity(self):
        reply = json.dumps({"firm": "EDF", "info": {"name": "EDIFIER S300", "uuid": 7, "host": "untrusted", "port": 8080}})
        result = parse_reply(reply, ("192.0.2.42", 6000))
        self.assertEqual((result['host'], result['id']), ('192.0.2.42', '7'))
        for bad in ('[]', '{}', 'not-json', '{"firm":"other","info":{}}'):
            self.assertIsNone(parse_reply(bad, ('192.0.2.42', 6000)))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.presets = Presets(Path(self.temp.name) / 'presets.json')
        self.controller = Controller(dict(DEFAULTS), self.presets)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.http.controller = self.controller
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:%s' % self.http.server_port
        self.opener = build_opener(ProxyHandler({}))

    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join(2)
        self.temp.cleanup()

    def request(self, path, body=None, headers=None):
        req = Request(self.base + path, None if body is None else json.dumps(body).encode(),
                      headers or ({'Content-Type': 'application/json'} if body is not None else {}))
        return self.opener.open(req, timeout=3)

    def test_offline_writes_rejected_and_never_queued(self):
        with self.assertRaises(HTTPError) as error:
            self.request('/api/eq', {'gains': [0] * 6})
        self.assertEqual(error.exception.code, 503)
        self.assertTrue(self.controller.jobs.empty())

    def test_wrong_content_type_and_oversized_requests_rejected(self):
        with self.assertRaises(HTTPError) as error:
            self.request('/api/discover', {}, {'Content-Type': 'text/plain'})
        self.assertEqual(error.exception.code, 415)
        with self.assertRaises(HTTPError) as error:
            self.request('/api/discover', {'data': 'x' * 9000})
        self.assertEqual(error.exception.code, 413)

    def test_invalid_volume_and_eq_return_400(self):
        for path, body in (('/api/eq', {'gains': [1] * 6}), ('/api/volume', {'volume': True}), ('/api/volume', {'volume': 31})):
            with self.assertRaises(HTTPError) as error: self.request(path, body)
            self.assertEqual(error.exception.code, 400)

    def test_presets_survive_restart_and_delete(self):
        with self.request('/api/presets', {'name': '夜间爵士', 'gains': [0, 5, 0, -5, 0, 0]}) as r:
            self.assertEqual(r.status, 200)
        reloaded = Presets(self.presets.path)
        entry = reloaded.list()[-1]
        self.assertEqual(entry['name'], '夜间爵士')
        self.assertEqual(entry['gains'], [0, 5, 0, -5, 0, 0])
        self.request('/api/presets/delete', {'id': entry['id']}).close()
        self.assertEqual(len(Presets(self.presets.path).list()), 6)

    def test_unknown_target_and_cross_origin_writes_rejected(self):
        with self.assertRaises(HTTPError) as error: self.request('/api/device', {'id': 'arbitrary-host'})
        self.assertEqual(error.exception.code, 400)
        with self.assertRaises(HTTPError) as error:
            self.request('/api/eq', {'gains': [0] * 6}, {'Content-Type': 'application/json', 'Origin': 'https://other.test'})
        self.assertEqual(error.exception.code, 403)

    def test_shell_manifest_cache_rules_and_no_file_traversal(self):
        for path in ('/', '/app.js', '/app.css', '/manifest.webmanifest', '/sw.js', '/healthz', '/api/state'):
            with self.request(path) as r:
                self.assertEqual(r.status, 200)
                self.assertIn(r.headers['Cache-Control'], ('no-store', 'no-cache'))
                if path == '/manifest.webmanifest': self.assertEqual(json.load(r)['start_url'], './')
        for path in ('/../server.py', '/%2e%2e/server.py'):
            with self.assertRaises(HTTPError) as error: self.request(path)
            self.assertEqual(error.exception.code, 404)

    def test_controller_reconnects_after_discovered_address_change(self):
        first, second = FakeSpeaker(), FakeSpeaker()
        config = dict(DEFAULTS, discovery_interval=.08, discovery_timeout=.03, poll_interval=.1, connect_timeout=.5)
        device = {'id': '7', 'name': 'Test S300', 'host': '127.0.0.1', 'port': first.port, 'discovered_at': time.time()}
        controller = Controller(config, self.presets)
        def found(_): return {'7': dict(device, discovered_at=time.time())}
        def wait_for(predicate):
            end = time.monotonic() + 4
            while time.monotonic() < end:
                if predicate(): return
                time.sleep(.02)
            self.fail('controller state did not converge')
        try:
            with patch('s300_control.server.discover', side_effect=found):
                controller.start()
                wait_for(lambda: controller.status == 'online')
                controller.submit('eq', {'gains': [5, 0, 0, 0, 0, 0]})
                self.assertEqual(gains_from(first.raw), [5, 0, 0, 0, 0, 0])
                device['port'] = second.port
                controller.rescan.set()
                wait_for(lambda: controller.device['port'] == second.port and controller.status == 'online')
                self.assertEqual(controller.snapshot()['state']['gains'], [0] * 6)
                self.assertEqual(first.writes, 1)
                self.assertEqual(second.writes, 0)  # Never replay old settings.
        finally:
            controller.stop.set(); controller.rescan.set()
            for thread in controller.threads: thread.join(2)
            first.close(); second.close()


if __name__ == '__main__': unittest.main()
