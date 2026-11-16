import json
import tempfile
import threading
import unittest
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from vaultsync.tts.arkham import Pack, is_arkham_sce, wanted
from vaultsync.tts.downloader import download_assets
from vaultsync.tts.naming import cache_stem, detect_folder, download_candidates, sniff_extension
from vaultsync.tts.package import PackageError, build_package, restore_package
from vaultsync.tts.mods import ModFile, list_workshop_mods, resolve_mod, safe_filename
from vaultsync.tts.scanner import find_assets

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\0" * 64


class NamingTest(unittest.TestCase):
    def test_cache_stem_matches_tts(self):
        # Real names taken from a TTS cache folder.
        self.assertEqual(
            cache_stem("https://steamusercontent-a.akamaihd.net/ugc/1298675058333285993/C4BC94488DFC6A60270A38AE00AA43C811CA5041/"),
            "httpssteamusercontentaakamaihdnetugc1298675058333285993C4BC94488DFC6A60270A38AE00AA43C811CA5041",
        )
        self.assertEqual(cache_stem("https://i.imgur.com/0Xzm6g2.jpg"), "httpsiimgurcom0Xzm6g2jpg")

    def test_legacy_steam_url_tries_akamai_first(self):
        url = "http://cloud-3.steamusercontent.com/ugc/1/ABC/"
        self.assertEqual(
            download_candidates(url),
            ["https://steamusercontent-a.akamaihd.net/ugc/1/ABC/", url],
        )

    def test_extensions(self):
        self.assertEqual(sniff_extension("Images", JPG, ""), ".jpg")
        self.assertEqual(sniff_extension("Images", PNG, ""), ".png")
        self.assertEqual(sniff_extension("Models", b"v 1 2 3", ""), ".obj")
        self.assertEqual(sniff_extension("Audio", b"OggS....", ""), ".ogg")
        self.assertEqual(sniff_extension("Audio", b"RIFF\0\0\0\0WAVE", ""), ".wav")

    def test_safe_filename(self):
        self.assertEqual(safe_filename('Netrunner  |  Custom: Mod?'), "Netrunner _ Custom_ Mod_")


class ScannerTest(unittest.TestCase):
    def test_finds_nested_assets(self):
        data = {
            "SkyURL": "https://x/sky.png",
            "MusicPlayer": {"CurrentAudioURL": "https://x/a.mp3", "AudioLibrary": [{"Item1": "https://x/b.ogg", "Item2": "B"}]},
            "CustomUIAssets": [{"Type": 1, "Name": "font", "URL": "https://x/font.unity3d"}],
            "ObjectStates": [
                {"CustomMesh": {"MeshURL": "https://x/m.obj", "DiffuseURL": "https://x/d.png", "ColliderURL": ""}},
                {"ContainedObjects": [{"CustomDeck": {"1": {"FaceURL": "https://x/f.jpg", "BackURL": "https://x/f.jpg"}}}]},
                {"States": {"2": {"CustomPDF": {"PDFUrl": "https://x/r.pdf"}}}},
            ],
        }
        self.assertEqual(
            find_assets(data),
            {
                "https://x/sky.png": "Images",
                "https://x/a.mp3": "Audio",
                "https://x/b.ogg": "Audio",
                "https://x/font.unity3d": "Assetbundles",
                "https://x/m.obj": "Models",
                "https://x/d.png": "Images",
                "https://x/f.jpg": "Images",
                "https://x/r.pdf": "PDF",
            },
        )


class ScriptLinkTest(unittest.TestCase):
    def test_links_in_scripts_have_unknown_folder(self):
        data = {
            "LuaScript": 'img = "https://steamusercontent-a.akamaihd.net/ugc/123/ABCDEF/" '
                         'api = "https://arkhamdb.com/api/public" base = "https://x/" .. id',
            "ObjectStates": [{
                "XmlUI": '<Image image="https://i.imgur.com/abc.png" />',
                "CustomImage": {"ImageURL": "https://i.imgur.com/abc.png"},
            }],
        }
        self.assertEqual(
            find_assets(data),
            {
                "https://i.imgur.com/abc.png": "Images",
                "https://steamusercontent-a.akamaihd.net/ugc/123/ABCDEF/": None,
            },
        )
        self.assertNotIn("https://steamusercontent-a.akamaihd.net/ugc/123/ABCDEF/",
                         find_assets(data, include_scripts=False))

    def test_detect_folder(self):
        self.assertEqual(detect_folder(PNG), "Images")
        self.assertEqual(detect_folder(b"UnityFS\0"), "Assetbundles")
        self.assertEqual(detect_folder(b"# obj\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"), "Models")
        self.assertIsNone(detect_folder(b'{"json": true}'))

    def test_arkham_language_filter(self):
        campaign = Pack("Night of the Zealot", "campaign", "notz")
        german = Pack("German - Campaigns", "language-pack", "de")
        french = Pack("French - Campaigns", "language-pack", "fr")
        self.assertTrue(wanted(campaign, []))
        self.assertEqual([wanted(p, ["German"]) for p in (german, french)], [True, False])
        self.assertEqual([wanted(p, ["all"]) for p in (german, french)], [True, True])
        self.assertTrue(is_arkham_sce({"LuaScript": "SOURCE_REPO = 'https://github.com/Chr1Z93/SCED-downloads/'"}))


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        www = root / "www"
        www.mkdir()
        (www / "card.jpg").write_bytes(JPG)
        (www / "board.png").write_bytes(PNG)
        (www / "error.png").write_text("<!DOCTYPE html><html>Not found</html>")
        (www / "noext.jpg").write_bytes(JPG)
        (www / "data.png").write_text('{"not": "an image"}')
        handler = partial(_QuietHandler, directory=str(www))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.server.server_port}"

        self.mods = root / "Mods"
        (self.mods / "Workshop").mkdir(parents=True)
        self.mod_json = self.mods / "Workshop" / "123.json"
        self.mod_json.write_text(json.dumps({
            "SaveName": "Test Mod",
            "TableURL": f"{base}/board.png",
            "LuaScript": f'a = "{base}/noext.jpg" b = "{base}/data.png"',
            "ObjectStates": [
                {"CustomDeck": {"1": {"FaceURL": f"{base}/card.jpg", "BackURL": f"{base}/error.png"}}},
                {"CustomImage": {"ImageURL": f"{base}/missing.png"}},
            ],
        }))
        (self.mods / "Workshop" / "123.png").write_bytes(PNG)
        self.base = base

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_download_backup_restore(self):
        assets = find_assets(json.loads(self.mod_json.read_text()))
        results = download_assets(self.mods, assets, workers=2, retries=1)
        by_url = {r.url: r for r in results}
        self.assertEqual(by_url[f"{self.base}/card.jpg"].path.suffix, ".jpg")
        self.assertEqual(by_url[f"{self.base}/board.png"].status, "downloaded")
        self.assertEqual(by_url[f"{self.base}/error.png"].status, "failed")
        self.assertEqual(by_url[f"{self.base}/missing.png"].status, "failed")
        # Script links: the type comes from the bytes; non-assets are skipped, not missing.
        self.assertEqual(by_url[f"{self.base}/noext.jpg"].folder, "Images")
        self.assertEqual(by_url[f"{self.base}/data.png"].status, "skipped")
        self.assertEqual(list(self.mods.glob("Images/*.part")), [])

        # A second run uses the cache.
        again = download_assets(self.mods, assets, workers=2, retries=1)
        self.assertEqual(sum(r.status == "cached" for r in again), 3)

        out = Path(self.tmp.name) / "out"
        zip_path = build_package(ModFile(self.mod_json, "workshop", "123"), results, out)
        self.assertEqual(zip_path.name, "Test Mod (123).zip")
        with zipfile.ZipFile(zip_path) as z:
            manifest = json.loads(z.read("manifest.json"))
            self.assertIn("Workshop/123.json", z.namelist())
            self.assertIn("Workshop/123.png", z.namelist())
        self.assertEqual(len(manifest["missing"]), 2)
        self.assertEqual(len(manifest["skipped_script_links"]), 1)

        target = Path(self.tmp.name) / "restored" / "Mods"
        report = restore_package(zip_path, target)
        self.assertTrue(report.index_updated)
        self.assertEqual(len(report.written), 5)
        for name in manifest["files"]:
            src = self.mods / name
            self.assertEqual((target / name).read_bytes(), src.read_bytes())
        index = json.loads((target / "Workshop" / "WorkshopFileInfos.json").read_text())
        self.assertEqual(index[0]["Name"], "Test Mod")

        report = restore_package(zip_path, target)
        self.assertEqual(len(report.written), 0)
        self.assertFalse(report.index_updated)

    def test_subfolder_mods_keep_their_folder(self):
        folder = self.mods / "Workshop" / "Campaign Games"
        folder.mkdir()
        named = folder / "My Campaign 1.2.json"
        named.write_text(json.dumps({"SaveName": "My Campaign"}))
        (folder / "456.json").write_text(json.dumps({"SaveName": "Other"}))

        mods = list_workshop_mods(self.mods)
        self.assertEqual(
            [(m.subfolder, m.id) for m in mods],
            [("", "123"), ("Campaign Games", "456"), ("Campaign Games", "My Campaign 1.2")],
        )
        self.assertEqual(resolve_mod("456", self.mods).subfolder, "Campaign Games")
        mod = resolve_mod(str(named), self.mods)
        self.assertEqual((mod.kind, mod.subfolder), ("workshop", "Campaign Games"))

        out = Path(self.tmp.name) / "out"
        zip_path = build_package(mod, [], out)
        self.assertEqual(zip_path, out / "Campaign Games" / "My Campaign.zip")

        target = Path(self.tmp.name) / "restored" / "Mods"
        restore_package(zip_path, target)
        self.assertTrue((target / "Workshop" / "Campaign Games" / "My Campaign 1.2.json").is_file())
        index = json.loads((target / "Workshop" / "WorkshopFileInfos.json").read_text())
        self.assertTrue(index[0]["Directory"].endswith("Workshop/Campaign Games/My Campaign 1.2.json"))

    def test_restore_rejects_path_traversal(self):
        bad = Path(self.tmp.name) / "bad.zip"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("manifest.json", json.dumps({"format": "tts-backup", "format_version": 1}))
            z.writestr("Images/../../evil.txt", "x")
        with self.assertRaises(PackageError):
            restore_package(bad, Path(self.tmp.name) / "r" / "Mods")
        self.assertFalse((Path(self.tmp.name) / "r" / "evil.txt").exists())


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


if __name__ == "__main__":
    unittest.main()
