#!/usr/bin/env python3
"""Query/control the backend; the backend owns discovery and the device socket."""
import argparse
import json
import os
from urllib.request import ProxyHandler, Request, build_opener


def main():
    parser = argparse.ArgumentParser(description="S300 control service client")
    parser.add_argument("--url", default=os.environ.get("S300_URL", "http://localhost:18730/"))
    parser.add_argument("--volume", type=int)
    parser.add_argument("--eq", type=int, nargs=6)
    parser.add_argument("--mode", type=int, choices=range(5),
                        help="0=Classic, 1=Monitor, 2=Dynamic, 3=Vocal, 4=DIY")
    args = parser.parse_args()
    opener = build_opener(ProxyHandler({}))
    base = args.url.rstrip("/") + "/api/"
    for path, body in (("mode", {"selected_index": args.mode}),
                       ("eq", {"gains": args.eq}), ("volume", {"volume": args.volume})):
        if next(iter(body.values())) is not None:
            request = Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
            with opener.open(request, timeout=15) as response:
                json.load(response)
    with opener.open(base + "state", timeout=5) as response:
        print(json.dumps(json.load(response), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
