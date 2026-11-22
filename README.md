# OSS Android Data Provider

OSS reads the public latest-release `catalog.json` without credentials. The library
uses one dropdown filtered to Beta or Release. Selecting a menu opens its installer.
Only menu versions are shown in app UI.

## Small downloads

From installer build 1.5.28.8, normal menu downloads use `patch` data, not the full
`zip`. Current patches are 2.4–4.6 MB. The existing original-byte restore data is
reused when its hash matches; otherwise the matching small `restore` payload is
cached once. Downloads are resumable, hash checked and available offline.

Each patch reuses the phone's existing archives, stages changes and verifies all
three complete output hashes before the existing transaction replaces any files.
Unknown or mixed source files are allowed when reconstruction verifies. If local
bytes cannot produce the exact target, the operation stops without replacing game
files or automatically downloading a full ZIP. Configs and counters are retained.

Full historical ZIPs remain immutable release assets for older clients and manual
installation. A current client never downloads them through Menu library. A menu
without patch metadata requests a catalog refresh instead.

## Publishing a menu

1. Scan verified ZIPs with `python provider.py scan --directory PATH --output INVENTORY`.
   Each ZIP contains exactly the three original pakchunk filenames. Assign each
   inventory record its correct `channel` and notes, based on release evidence.
2. Publish the ZIP assets with `python provider.py publish --output INVENTORY`.
   Matching historical assets and existing patch metadata are preserved.
3. Prepare the complete menu inventory (including earlier versions) with:
   `python patches.py prepare --inventory INVENTORY/inventory.local.json --originals ORIGINALS --manifest payload-manifest.json --compatibility compatibility-blocks.json --output PATCHES`.
   Use the installer manifest containing the pinned original file hashes. Originals
   are read-only. Include every published menu in the patch inventory so upgrades,
   downgrades and restores include the union of changed blocks. The preparation
   checks complete target hashes and every source/target block sequence.
4. Run `python patches.py publish --output PATCHES`. It uploads immutable patch
   assets, verifies their digests, merges the latest catalog, and publishes it last.
   Complete this step before announcing the menu as available in the current app.

Set `GH_EXE` if GitHub CLI is not on PATH. Future compatible menu releases require
these data-publishing steps, not another APK build. Never overwrite a version with
different bytes. Keep ZIPs and patches in releases, not source history. Local
inventories contain filesystem paths and must not be committed.

## Schema 1 compatibility

The required `zip` and exact three `files` fields remain for older clients. New
`patch` and `restore` descriptors contain `format: oss-blocks-v1`, URL, byte count,
SHA-256 and optional GitHub asset ID. Patch filenames include their content hash;
restore payloads are shared by hash. Each compressed patch is limited to 8 MiB.
The app verifies a provider restore against its pinned supplied-original hashes.

The app's APK updater and the menu provider remain separate. Public menu downloads
need no token. Never include publishing credentials or local secret files in source.

## Validation

Run `python -m unittest discover -p 'test_*.py'`. Fixtures cover immutable publication,
full target hashing, cross-version patching and exact-original restore. Installer
checks additionally exercise actual public patch downloads and real archive
transactions inside an isolated emulator. These are not Android gameplay tests.
