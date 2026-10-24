#!/usr/bin/env python3
"""Run the real UI against synthetic, in-memory device state. No network discovery."""
import argparse
import copy
from pathlib import Path
import sys
import tempfile
import threading
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from s300_control.protocol import eq_fields
from s300_control.server import Controller, DEFAULTS, Handler, Presets, ThreadingHTTPServer


class PreviewController(Controller):
    def submit(self, kind, body):
        with self.lock:
            raw = copy.deepcopy(self.raw)
            if kind == "eq":
                raw.update(eq_fields(body["gains"], raw))
            elif kind == "volume":
                raw["player"]["volume"] = body["volume"]
            self._ingest(raw)
            return self.snapshot()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18731)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="s300-preview-") as folder:
        presets = Presets(Path(folder) / "presets.json")
        presets.save({"name": "晚间爵士", "gains": [0, 5, 5, 0, -5, -5]})
        presets.save({"name": "轻柔人声", "gains": [-5, 0, 10, 5, 0, -5]})
        controller = PreviewController(dict(DEFAULTS), presets)
        device = {"id": "demo", "name": "EDIFIER S300", "host": "192.0.2.42", "port": 8080,
                  "mac": "02:00:00:00:00:01", "discovered_at": time.time()}
        controller.device, controller.devices = device, {"demo": device}
        controller._ingest({"deviceInfo": {"airplayName": "EDIFIER S300", "firmwareVersion": "01.13.07", "wifiName": "Example Wi-Fi"},
                            "player": {"volume": 8, "minVolume": 0, "maxVolume": 30},
                            **eq_fields([-5, 0, 10, 5, 0, -5], {})})
        # Deliberately do not start Controller's discovery or TCP threads.
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
        server.controller = controller
        print(f"Synthetic UI preview: http://127.0.0.1:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
