"""Verify shipped install assets using the same dependency-free runtime as the app."""
import json
from html.parser import HTMLParser
from pathlib import Path
import re
import struct
import unittest

import s300_control

STATIC = Path(s300_control.__file__).parent / "static"


class Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "link":
            self.links.append(dict(attrs))


class InstallAssetTests(unittest.TestCase):
    def test_manifest_icons_exist_at_the_declared_resolution(self):
        manifest = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
        sizes, purposes = set(), set()
        for icon in manifest["icons"]:
            path = STATIC / icon["src"].removeprefix("./")
            data = path.read_bytes()
            if icon["type"] == "image/png":
                self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
                width, height = struct.unpack("!II", data[16:24])
                self.assertEqual(icon["sizes"], f"{width}x{height}")
                sizes.add(width)
            purposes.add(icon["purpose"])
        self.assertTrue({192, 512, 1024}.issubset(sizes))
        self.assertIn("maskable", purposes)
        self.assertEqual(manifest["scope"], "./")

    def test_single_high_resolution_apple_icon_and_html_asset_links(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        for url in re.findall(r'(?:src|href)="(\./[^"#]+)"', html):
            self.assertTrue((STATIC / url[2:]).is_file(), url)
        apple_icons = [link for link in Links(html).links
                       if "apple-touch-icon" in link.get("rel", "").split()]
        self.assertEqual(len(apple_icons), 1)
        icon_url = apple_icons[0]["href"]
        self.assertEqual(icon_url, "./icons/v2/apple-touch-icon-1024-v3.png")
        high_resolution = (STATIC / icon_url.removeprefix("./")).read_bytes()
        self.assertEqual(high_resolution[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack("!II", high_resolution[16:24]), (1024, 1024))
        self.assertEqual(high_resolution[25], 2)  # RGB, no transparency.

    def test_worker_precaches_the_current_apple_icon_and_existing_assets(self):
        worker = (STATIC / "sw.js").read_text(encoding="utf-8")
        assets = json.loads(re.search(r"const ASSETS = (\[.*?\]);", worker, re.S)
                            .group(1).replace(",\n]", "\n]"))
        apple_icons = [asset for asset in assets if "apple-touch-icon" in asset]
        self.assertEqual(apple_icons, ["./icons/v2/apple-touch-icon-1024-v3.png"])
        for asset in assets:
            self.assertTrue((STATIC / asset.removeprefix("./")).exists(), asset)


if __name__ == "__main__":
    unittest.main()
