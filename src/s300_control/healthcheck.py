"""Health probe shared by the Docker image and custom deployments."""
import os
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from .server import load_config


def main():
    path = Path(os.getenv("S300_CONFIG", str(Path.cwd() / "config" / "settings.json")))
    config = load_config(path)
    host = "127.0.0.1" if config["bind"] == "0.0.0.0" else config["bind"]
    with build_opener(ProxyHandler({})).open(
            "http://%s:%s/healthz" % (host, config["port"]), timeout=3) as response:
        if response.status != 200:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
