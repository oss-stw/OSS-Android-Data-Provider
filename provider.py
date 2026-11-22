"""Prepare and publish data-only menu releases. Never builds or updates an APK."""
from pathlib import Path
import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import zipfile
from datetime import datetime, timezone

NAMES = ["pakchunk30-Android_ASTCClient_s1.ucas", "pakchunk30-Android_ASTCClient_s2.ucas", "pakchunk30-Android_ASTCClient.utoc"]


def digest(stream):
    return hashlib.file_digest(stream, "sha256").hexdigest()


def inspect(path, repository, channel="beta", notes=""):
    path = Path(path)
    match = re.fullmatch(r"menu_v([0-9]{1,6}(?:\.[0-9]{1,6}){1,3})\.zip", path.name)
    if not match or not 0 < path.stat().st_size < 500_000_000:
        raise ValueError(f"Invalid menu ZIP: {path}")
    version = match[1]
    files = []
    with zipfile.ZipFile(path) as archive:
        if len(archive.namelist()) != 3 or set(archive.namelist()) != set(NAMES):
            raise ValueError(f"Expected exactly the three original filenames: {path}")
        for name in NAMES:
            info = archive.getinfo(name)
            if not 0 < info.file_size <= 2_000_000_000:
                raise ValueError(f"Invalid archive size: {name}")
            with archive.open(name) as stream:
                sha = digest(stream)  # Also verifies each ZIP CRC while reading.
            files.append({"name": name, "bytes": info.file_size, "sha256": sha})
    if sum(item["bytes"] for item in files) > 4_000_000_000:
        raise ValueError("Menu exceeds protocol limit")
    with path.open("rb") as stream:
        sha = digest(stream)
    return {"version": version, "channel": channel, "notes": notes,
            "zip": {"url": f"https://github.com/{repository}/releases/download/menu-v{version}/{path.name}",
                    "bytes": path.stat().st_size, "sha256": sha}, "files": files}


def gh(*args, json_result=False):
    result = subprocess.run([os.environ.get("GH_EXE", "gh"), *args], check=True, capture_output=True, text=True)
    return json.loads(result.stdout) if json_result else result.stdout


def release_by_tag(repository, tag):
    # GitHub's tag endpoint does not resolve unpublished draft tags.
    for attempt in range(5):
        try:
            item = gh("release", "view", tag, "--repo", repository, "--json", "databaseId", json_result=True)
            return gh("api", f"repos/{repository}/releases/{item['databaseId']}", json_result=True)
        except subprocess.CalledProcessError:
            if attempt == 4:
                raise
            time.sleep(2)


def publish(inventory, repository, output):
    """Reuse matching assets; never replace a historical menu with different bytes."""
    existing = gh("api", f"repos/{repository}/releases?per_page=100", "--paginate", "--slurp", json_result=True)
    releases = {release["tag_name"]: release for page in existing for release in page}
    catalog = {"schema": 1, "menus": []}
    # Preserve provider entries that aren't present in this local import.
    current = next((r for r in releases.values() if r["tag_name"].startswith("catalog-") and not r["draft"]), None)
    if current:
        asset = next((a for a in current["assets"] if a["name"] == "catalog.json"), None)
        if not asset:
            raise ValueError("Existing catalog release has no catalog.json")
        previous = json.loads(gh("api", f"repos/{repository}/releases/assets/{asset['id']}", "-H", "Accept: application/octet-stream"))
        if previous["schema"] != 1:
            raise ValueError("Unsupported existing catalog schema")
        catalog["menus"] = previous["menus"]
    indexed = {m["version"]: m for m in catalog["menus"]}
    def upload_one(record):
        path = Path(record["path"])
        menu = inspect(path, repository, record["menu"]["channel"], record["menu"]["notes"])
        if menu != record["menu"]:
            raise ValueError(f"Archive changed since inspection: {path}")
        tag = "menu-v" + menu["version"]
        release = releases.get(tag)
        if release is None:
            with tempfile.TemporaryDirectory() as temporary:
                notes = Path(temporary) / "notes.txt"
                notes.write_text(menu["notes"] or f"Menu v{menu['version']}", encoding="utf-8")
                gh("release", "create", tag, "--repo", repository, "--draft", "--title", f"Menu v{menu['version']}", "--notes-file", str(notes))
            release = release_by_tag(repository, tag)
        asset = next((a for a in release["assets"] if a["name"] == path.name), None)
        if asset is None:
            gh("release", "upload", tag, str(path), "--repo", repository)
            release = gh("api", f"repos/{repository}/releases/{release['id']}", json_result=True)
            asset = next(a for a in release["assets"] if a["name"] == path.name)
        if asset["size"] != menu["zip"]["bytes"] or asset.get("digest") != "sha256:" + menu["zip"]["sha256"]:
            raise ValueError(f"Existing remote menu differs; preserved: {tag}")
        if release["draft"]:
            gh("release", "edit", tag, "--repo", repository, "--draft=false", "--latest=false", "--prerelease=" + str(menu["channel"] == "beta").lower())
        menu["zip"]["assetId"] = asset["id"]
        print(f"Verified {tag}", flush=True)
        return menu
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        for menu in executor.map(upload_one, inventory):
            previous=indexed.get(menu["version"], {})
            if previous.get("files")==menu["files"] and previous.get("zip",{}).get("sha256")==menu["zip"]["sha256"]:
                for key in ("patch","restore"):
                    if key in previous:menu[key]=previous[key]
            indexed[menu["version"]] = menu
    catalog["menus"] = sorted(indexed.values(), key=lambda m: tuple(map(int, m["version"].split("."))), reverse=True)
    publish_catalog(catalog, repository, output)


def publish_catalog(catalog, repository, output):
    payload = json.dumps(catalog, indent=2) + "\n"
    if len(payload.encode("utf-8")) > 1024 * 1024:
        raise ValueError("Catalog exceeds app limit; nothing published as latest")
    output.mkdir(parents=True, exist_ok=True)
    destination = output / "catalog.json"
    destination.write_bytes(payload.encode("utf-8"))
    tag = "catalog-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    with tempfile.TemporaryDirectory() as temporary:
        notes = Path(temporary) / "notes.txt"
        notes.write_text(f"{len(catalog['menus'])} downloadable menu versions. Refresh Menu library in OSS Android.", encoding="utf-8")
        gh("release", "create", tag, str(destination), "--repo", repository, "--draft", "--title", "Menu catalog", "--notes-file", str(notes))
    release = release_by_tag(repository, tag)
    asset = next(a for a in release["assets"] if a["name"] == "catalog.json")
    expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if asset.get("digest") != "sha256:" + expected:
        raise ValueError("Catalog upload did not verify; draft retained")
    gh("release", "edit", tag, "--repo", repository, "--draft=false", "--latest")
    print(f"Published {len(catalog['menus'])} menus: https://github.com/{repository}/releases/tag/{tag}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["scan", "publish"])
    parser.add_argument("--repository", default="oss-stw/OSS-Android-Data-Provider")
    parser.add_argument("--directory", action="append", type=Path, default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--channel", choices=["release", "beta"], default="beta")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
        parser.error("Invalid repository")
    inventory_file = args.output / "inventory.local.json"
    if args.command == "publish":
        publish(json.loads(inventory_file.read_text(encoding="utf-8")), args.repository, args.output)
        return
    records = {}
    for directory in args.directory:
        for path in sorted(directory.glob("menu_v*.zip")):
            menu = inspect(path, args.repository, args.channel)
            previous = records.get(menu["version"])
            if previous and previous["menu"] != menu:
                raise ValueError(f"Conflicting archives for v{menu['version']}; choose the verified final release")
            records[menu["version"]] = {"path": str(path.resolve()), "menu": menu}
            print(f"Verified menu v{menu['version']}", flush=True)
    args.output.mkdir(parents=True, exist_ok=True)
    inventory_file.write_text(json.dumps(list(records.values()), indent=2) + "\n", encoding="utf-8")
    (args.output / "catalog.preview.json").write_text(json.dumps({"schema": 1, "menus": [r["menu"] for r in records.values()]}, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {len(records)} menus; no GitHub writes performed.")


if __name__ == "__main__":
    main()
