import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import provider


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.zip = self.root / "menu_v9.99.zip"
        with zipfile.ZipFile(self.zip, "w") as archive:
            for name in provider.NAMES:
                archive.writestr(name, name.encode())
        self.repo = "oss-stw/OSS-Android-Data-Provider"

    def test_hashes_each_exact_archive(self):
        menu = provider.inspect(self.zip, self.repo)
        self.assertEqual("9.99", menu["version"])
        self.assertEqual(provider.NAMES, [f["name"] for f in menu["files"]])
        for file in menu["files"]:
            self.assertEqual(hashlib.sha256(file["name"].encode()).hexdigest(), file["sha256"])

    def test_unexpected_zip_entries_rejected(self):
        with zipfile.ZipFile(self.zip, "a") as archive:
            archive.writestr("../config.ini", "bad")
        with self.assertRaises(ValueError):
            provider.inspect(self.zip, self.repo)

    def test_changed_local_payload_rejected_before_upload(self):
        record = {"path": str(self.zip), "menu": provider.inspect(self.zip, self.repo)}
        record["menu"]["zip"]["sha256"] = "0" * 64
        with patch.object(provider, "gh", return_value=[[]]) as gh:
            with self.assertRaises(ValueError):
                provider.publish([record], self.repo, self.root / "out")
            self.assertEqual(1, gh.call_count)  # Read-only release inventory, no mutations.

    def test_existing_different_remote_menu_never_overwritten(self):
        record = {"path": str(self.zip), "menu": provider.inspect(self.zip, self.repo)}
        release = {"tag_name": "menu-v9.99", "draft": False,
                   "assets": [{"name": self.zip.name, "size": self.zip.stat().st_size, "digest": "sha256:" + "0" * 64}]}
        with patch.object(provider, "gh", return_value=[[release]]) as gh:
            with self.assertRaises(ValueError):
                provider.publish([record], self.repo, self.root / "out")
            self.assertEqual(1, gh.call_count)


if __name__ == "__main__":
    unittest.main()
