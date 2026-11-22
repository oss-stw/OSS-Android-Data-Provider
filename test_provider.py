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

    def test_draft_release_lookup_uses_database_id(self):
        release = {"id": 42, "tag_name": "menu-v9.99", "draft": True}
        with patch.object(provider, "gh", side_effect=[{"databaseId": 42}, release]) as gh:
            self.assertEqual(release, provider.release_by_tag(self.repo, "menu-v9.99"))
            self.assertEqual("release", gh.call_args_list[0].args[0])
            self.assertEqual(f"repos/{self.repo}/releases/42", gh.call_args_list[1].args[1])

    def test_published_catalog_hash_matches_exact_uploaded_bytes(self):
        menu=provider.inspect(self.zip,self.repo)
        release={"id":42,"tag_name":"menu-v9.99","draft":False,"assets":[{
            "id":123,"name":self.zip.name,"size":self.zip.stat().st_size,"digest":"sha256:"+menu["zip"]["sha256"]}]}
        output=self.root/"output"
        def lookup(repository,tag):
            data=(output/"catalog.json").read_bytes()
            self.assertNotIn(b"\r\n",data)
            self.assertEqual(123,json.loads(data)["menus"][0]["zip"]["assetId"])
            return {"assets":[{"name":"catalog.json","digest":"sha256:"+hashlib.sha256(data).hexdigest()}]}
        def call(*args,**kwargs):
            if args[0]=="api":return [[release]]
            return ""
        with patch.object(provider,"gh",side_effect=call) as gh,patch.object(provider,"release_by_tag",side_effect=lookup):
            provider.publish([{"path":str(self.zip),"menu":menu}],self.repo,output)
            self.assertEqual(("release","edit"),gh.call_args_list[-1].args[:2])
            self.assertIn("--latest",gh.call_args_list[-1].args)


if __name__ == "__main__":
    unittest.main()
