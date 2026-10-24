import copy
from contextlib import contextmanager
import json
import re
import struct
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
from s300_control.protocol import BANDS, DeviceError, FrameParser, SpeakerConnection, encode, eq_fields, gains_from, mode_fields, mode_from, new_id, xor
from s300_control.server import ControlConflict, Controller, DEFAULTS, Handler, ModeConflict, Presets, THEME_COLORS, ThreadingHTTPServer, load_config, validate_gains


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
        self.settings = []
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
                            self.settings.append(copy.deepcopy(obj))
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


class QueryThenModePush:
    """Socket peer that includes a newer factory-mode push after its DIY reply."""
    def __init__(self):
        self.raw, self.buffer, self.settings, self.first = state(), b'', [], True

    def setsockopt(self, *_): pass
    def settimeout(self, *_): pass
    def close(self): pass

    def sendall(self, data):
        request = json.loads(xor(data))
        if request['payload'] == 'settings':
            self.settings.append(request)
            for key in ('soundEffect', 'player'):
                if key in request:
                    self.raw[key].update(request[key])
            self.buffer += frame({'id': request['id'], 'code': 0})
        else:
            self.buffer += frame(dict(copy.deepcopy(self.raw), id=request['id'], code=0))
            if self.first:
                self.first = False
                self.raw['soundEffect'].update(mode_fields(1)['soundEffect'])
                self.buffer += frame(dict(copy.deepcopy(self.raw), payload='status_push'))

    def recv(self, _):
        data, self.buffer = self.buffer, b''
        return data


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

    def test_modes_preserve_the_stored_diy_curve(self):
        speaker = FakeSpeaker()
        speaker.raw.update(eq_fields([-25, -10, 30, 10, 10, 0], {}))
        connection = SpeakerConnection({"host": "127.0.0.1", "port": speaker.port}, 1)
        try:
            stored = copy.deepcopy(speaker.raw['soundEffect']['soundEffectDIY'])
            for index in range(5):
                result = connection.setting(mode_fields(index), lambda s: mode_from(s) == index)
                self.assertEqual(mode_from(result), index)
                self.assertEqual(result['soundEffect']['soundEffectDIY'], stored)
                self.assertEqual(speaker.settings[-1]['soundEffect'], {'soundIndex': 1, 'selectedIndex': index})
        finally:
            connection.close(); speaker.close()

    def test_ignored_mode_switch_fails_readback_verification(self):
        speaker = FakeSpeaker(ignore=True)
        connection = SpeakerConnection({"host": "127.0.0.1", "port": speaker.port}, 1)
        try:
            with self.assertRaises(DeviceError):
                connection.setting(mode_fields(1), lambda s: mode_from(s) == 1)
        finally:
            connection.close(); speaker.close()

    def test_query_keeps_a_newer_status_push_after_its_matching_reply(self):
        peer, observed = QueryThenModePush(), []
        with patch('s300_control.protocol.socket.create_connection', return_value=peer):
            connection = SpeakerConnection({'host': '127.0.0.1', 'port': 1}, .5,
                                           lambda current: observed.append(mode_from(current)))
            try:
                self.assertEqual(mode_from(connection.query()), 1)
                self.assertEqual(observed, [4, 1])
            finally:
                connection.close()

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

    @contextmanager
    def connected_speaker(self, mode=4):
        speaker = FakeSpeaker()
        speaker.raw['soundEffect'].update(mode_fields(mode)['soundEffect'])
        config = dict(DEFAULTS, discovery_interval=.1, discovery_timeout=.03, poll_interval=20, connect_timeout=.5)
        controller = Controller(config, self.presets)
        device = {'id': '7', 'name': 'Test S300', 'host': '127.0.0.1', 'port': speaker.port}
        def found(_): return {'7': dict(device, discovered_at=time.time())}
        try:
            with patch('s300_control.server.discover', side_effect=found):
                self.http.controller = controller
                controller.start()
                deadline = time.monotonic() + 3
                while controller.status != 'online' and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertEqual(controller.status, 'online')
                yield speaker, controller
        finally:
            controller.stop.set(); controller.rescan.set()
            for thread in controller.threads: thread.join(2)
            self.http.controller = self.controller
            speaker.close()

    def test_offline_writes_rejected_and_never_queued(self):
        with self.assertRaises(HTTPError) as error:
            self.request('/api/eq', {'gains': [0] * 6})
        self.assertEqual(error.exception.code, 503)
        self.assertTrue(self.controller.jobs.empty())

    def test_state_identifies_backend_restart_independently_of_revision(self):
        with self.request('/api/state') as response:
            first = json.load(response)
        with self.request('/api/state') as response:
            self.assertEqual(json.load(response)['instance_id'], first['instance_id'])
        self.http.controller = Controller(dict(DEFAULTS), self.presets)
        with self.request('/api/state') as response:
            restarted = json.load(response)
        self.assertNotEqual(first['instance_id'], restarted['instance_id'])
        self.assertEqual((first['revision'], restarted['revision']), (0, 0))

    def test_disconnect_and_selection_advance_snapshot_ordering(self):
        self.controller._ingest(state())
        online = self.controller.snapshot()
        self.controller._disconnect('connection lost')
        offline = self.controller.snapshot()
        self.assertGreater(offline['revision'], online['revision'])
        self.assertEqual(offline['connection'], 'offline')
        self.controller.devices['other'] = {'id': 'other'}
        self.controller.select('other')
        self.assertGreater(self.controller.snapshot()['revision'], offline['revision'])

    def test_theme_is_configured_and_invalid_names_are_rejected(self):
        path = Path(self.temp.name) / 'settings.json'
        self.assertEqual(load_config(path)['theme'], 'black')
        for theme in THEME_COLORS:
            path.write_text(json.dumps({'theme': theme}), encoding='utf8')
            self.assertEqual(load_config(path)['theme'], theme)
        for value in ('auto', 'dark', '', None, [], True):
            path.write_text(json.dumps({'theme': value}), encoding='utf8')
            with self.assertRaises(ValueError): load_config(path)

    def test_configured_theme_reaches_html_status_bar_and_manifest(self):
        for theme, background in THEME_COLORS.items():
            self.controller.config['theme'] = theme
            with self.request('/') as response:
                html = response.read().decode('utf8')
            self.assertIn('data-theme="' + theme + '"', html)
            self.assertIn('name="theme-color" content="' + background + '"', html)
            self.assertIn('content="default"' if theme == 'white' else 'content="black-translucent"', html)
            with self.request('/manifest.webmanifest') as response:
                manifest = json.load(response)
            self.assertEqual((manifest['theme_color'], manifest['background_color']), (background, background))

    def test_configured_theme_selects_apple_manifest_and_worker_icons(self):
        for theme in THEME_COLORS:
            self.controller.config['theme'] = theme
            prefix = './icons/v4/' + theme + '/'
            with self.request('/') as response:
                html = response.read().decode('utf8')
            apple = re.findall(r'<link\s+rel="apple-touch-icon".*?href="([^"]+)"', html, re.S)
            self.assertEqual(apple, [prefix + 'apple-touch-icon-1024.png'])
            for url in re.findall(r'(?:src|href)="(\./icons/[^"]+)"', html):
                self.assertTrue(url.startswith(prefix), url)
            with self.request(apple[0].removeprefix('.')) as response:
                png = response.read()
                self.assertEqual(response.headers['Content-Type'], 'image/png')
            self.assertEqual(struct.unpack('!II', png[16:24]), (1024, 1024))
            self.assertEqual(png[25], 2)
            with self.request('/manifest.webmanifest') as response:
                for icon in json.load(response)['icons']:
                    self.assertTrue(icon['src'].startswith(prefix), icon['src'])
            with self.request('/sw.js') as response:
                worker = response.read().decode('utf8')
            self.assertIn('const CACHE = "s300-shell-v10-' + theme + '"', worker)
            self.assertIn(prefix + 'apple-touch-icon-1024.png', worker)
            if theme != 'black':
                self.assertNotIn('./icons/v4/black/', worker)

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

    def test_invalid_modes_return_400(self):
        for index in (None, True, -1, 5, 4.0, '4'):
            with self.assertRaises(HTTPError) as error:
                self.request('/api/mode', {'selected_index': index})
            self.assertEqual(error.exception.code, 400)

    def test_mode_api_keeps_curve_and_requires_diy_before_eq(self):
        with self.connected_speaker(mode=1) as (speaker, controller):
            with self.assertRaises(HTTPError) as error:
                self.request('/api/eq', {'gains': [5, 0, 0, 0, 0, 0]})
            self.assertEqual(error.exception.code, 409)
            self.assertEqual(speaker.writes, 0)
            self.assertEqual(controller.status, 'online')
            with self.request('/api/mode', {'selected_index': 4}) as response:
                snapshot = json.load(response)
            self.assertTrue(snapshot['state']['eq_active'])
            self.assertEqual([m['id'] for m in snapshot['sound_modes']], list(range(5)))
            with self.request('/api/eq', {'gains': [5, 0, 0, 0, 0, 0]}) as response:
                self.assertEqual(json.load(response)['state']['gains'], [5, 0, 0, 0, 0, 0])
            with self.request('/api/mode', {'selected_index': 2}) as response:
                snapshot = json.load(response)
            self.assertFalse(snapshot['state']['eq_active'])
            self.assertEqual(snapshot['state']['selected_index'], 2)
            self.assertEqual(snapshot['state']['gains'], [5, 0, 0, 0, 0, 0])
            self.assertEqual(speaker.connections, 1)

    def test_eq_rechecks_mode_after_an_external_change(self):
        with self.connected_speaker() as (speaker, controller):
            self.assertTrue(controller.snapshot()['state']['eq_active'])
            speaker.raw['soundEffect'].update(mode_fields(3)['soundEffect'])
            with self.assertRaises(HTTPError) as error:
                self.request('/api/eq', {'gains': [5, 0, 0, 0, 0, 0]})
            self.assertEqual(error.exception.code, 409)
            self.assertEqual(speaker.writes, 0)
            self.assertEqual(controller.snapshot()['state']['selected_index'], 3)
            self.assertEqual(controller.status, 'online')

    def test_browser_controls_reject_stale_connection_context_before_queueing(self):
        with self.connected_speaker() as (speaker, controller):
            current = controller.snapshot()
            context = {'instance_id': current['instance_id'],
                       'connection_epoch': current['connection_epoch'], 'device_id': current['device']['id']}
            for field, value in (('instance_id', 'previous-process'), ('connection_epoch', -1), ('device_id', 'previous-device')):
                with self.assertRaises(HTTPError) as error:
                    self.request('/api/mode', {'selected_index': 1, 'context': dict(context, **{field: value})})
                self.assertEqual(error.exception.code, 409)
            self.assertEqual(speaker.writes, 0)
            self.assertTrue(controller.jobs.empty())
            with self.request('/api/mode', {'selected_index': 1, 'context': context}) as response:
                self.assertEqual(json.load(response)['state']['selected_index'], 1)

    def test_malformed_control_context_is_rejected(self):
        with self.connected_speaker() as (speaker, _):
            for context in (None, [], {}, {'instance_id': 'x', 'connection_epoch': True, 'device_id': '7'}):
                with self.assertRaises(HTTPError) as error:
                    self.request('/api/mode', {'selected_index': 1, 'context': context})
                self.assertEqual(error.exception.code, 400)
            self.assertEqual(speaker.writes, 0)

    def test_newer_factory_push_prevents_eq_write(self):
        peer = QueryThenModePush()
        with patch('s300_control.protocol.socket.create_connection', return_value=peer):
            connection = SpeakerConnection({'host': '127.0.0.1', 'port': 1}, .5, self.controller._ingest)
            self.controller.connection = connection
            try:
                with self.assertRaises(ModeConflict):
                    self.controller._execute('eq', {'gains': [5, 0, 0, 0, 0, 0]})
                self.assertEqual(peer.settings, [])
                self.assertEqual(self.controller.snapshot()['state']['selected_index'], 1)
            finally:
                connection.close()

    def test_queued_mode_is_rejected_after_same_device_reconnect(self):
        started, release, writes, results = threading.Event(), threading.Event(), [], {}

        class BlockingPeer:
            def __init__(self, device, *_):
                self.port, self.raw = device['port'], state()
            def query(self): return copy.deepcopy(self.raw)
            def close(self): pass
            def setting(self, fields, matches):
                if self.port == 1:
                    started.set()
                    if not release.wait(3): raise AssertionError('blocked test write timed out')
                writes.append((self.port, copy.deepcopy(fields)))
                for key, value in fields.items(): self.raw[key].update(value)
                if not matches(self.raw): raise AssertionError('simulated setting did not match')
                return self.query()

        controller = Controller(dict(DEFAULTS, poll_interval=20), self.presets)
        controller.devices = {'same': {'id': 'same', 'host': '127.0.0.1', 'port': 1}}
        def submit(kind, body):
            try: results[kind] = controller.submit(kind, body)
            except Exception as exc: results[kind] = exc
        control = threading.Thread(target=controller._control_loop, daemon=True)
        volume = threading.Thread(target=submit, args=('volume', {'volume': 7}))
        mode = threading.Thread(target=submit, args=('mode', {'selected_index': 1}))
        with patch('s300_control.server.SpeakerConnection', BlockingPeer):
            try:
                control.start()
                deadline = time.monotonic() + 3
                while controller.status != 'online' and time.monotonic() < deadline: time.sleep(.01)
                self.assertEqual(controller.status, 'online')
                volume.start()
                self.assertTrue(started.wait(2))
                mode.start()
                deadline = time.monotonic() + 2
                while controller.jobs.empty() and time.monotonic() < deadline: time.sleep(.01)
                self.assertFalse(controller.jobs.empty())
                with controller.lock: controller.devices['same']['port'] = 2
                release.set()
                volume.join(3); mode.join(3)
                self.assertIsInstance(results['mode'], ControlConflict)
                self.assertEqual([port for port, _ in writes], [1])
                self.assertEqual(controller.status, 'online')
                fresh = controller.submit('mode', {'selected_index': 1})
                self.assertEqual(fresh['state']['selected_index'], 1)
                self.assertEqual(writes[-1], (2, mode_fields(1)))
            finally:
                release.set()
                controller.stop.set()
                control.join(2)
                if volume.ident: volume.join(2)
                if mode.ident: mode.join(2)

    def test_presets_survive_restart_and_delete(self):
        before = self.controller.snapshot()['revision']
        with self.request('/api/presets', {'name': '夜间爵士', 'gains': [0, 5, 0, -5, 0, 0]}) as r:
            self.assertEqual(r.status, 200)
            saved_revision = json.load(r)['revision']
            self.assertGreater(saved_revision, before)
        reloaded = Presets(self.presets.path)
        entry = reloaded.list()[-1]
        self.assertEqual(entry['name'], '夜间爵士')
        self.assertEqual(entry['gains'], [0, 5, 0, -5, 0, 0])
        self.request('/api/presets/delete', {'id': entry['id']}).close()
        self.assertEqual(len(Presets(self.presets.path).list()), 6)
        self.assertGreater(self.controller.snapshot()['revision'], saved_revision)

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
