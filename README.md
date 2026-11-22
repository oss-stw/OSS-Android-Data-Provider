# OSS Android Data Provider

OSS Android reads `catalog.json` from this repository's latest GitHub release.
Public catalogs and ZIPs use direct release download links without credentials or
GitHub API quota. The initial supported app build is OSS Android 1.5.28.3.
Every entry appears in Menu library, including historical and Beta versions.
The app downloads the selected `menu_vX.Y.zip`, verifies its size and SHA-256,
then verifies all three extracted archives before using the existing installation
transaction. Downloads are resumable and cached. Configs and counters stay intact.
Restore originals remains the independently verified original reconstruction.

The initial app upgrade adds this protocol. Subsequent menu releases using schema 1
do not require an APK update. App features or a future incompatible protocol still do.

## Publishing

Keep large ZIPs in GitHub Releases, not in Git history. Each menu release has tag
`menu-vX.Y` and asset `menu_vX.Y.zip`. That ZIP contains exactly the three original
pakchunk filenames, with no metadata or subdirectories. Catalog releases have
unique `catalog-...` tags; only the completed catalog becomes Latest.

Run `python provider.py scan --directory PATH_TO_RELEASE_ZIPS --output PATH_TO_STAGING`.
Repeat `--directory` to combine preserved releases. The scan reads every ZIP entry,
verifies CRCs and hashes, and rejects conflicting copies of the same version.
Historical imports default to Beta; set each inventory entry's `channel` and
`notes` from its release evidence before publishing. These are display fields.
All versions remain selectable regardless of the APK's update channel.

Run `python provider.py publish --output PATH_TO_STAGING` after the repository exists.
Set `GH_EXE` if GitHub CLI is not on PATH. It uses the CLI's publishing credentials,
reuses matching assets, rejects altered historical assets and preserves catalog
entries not present in the local import. The catalog is published last so users
never see entries whose ZIP upload is incomplete. No APK is built or published.

Future menus follow the same scan/publish process. Never overwrite an existing
version with different bytes; assign a new version.

## App connection

`work/menu-installer/release.json` selects `menuRepository` (default
`oss-stw/OSS-Android-Data-Provider`). Public downloads need no credential.
For a private provider, supply a repository-scoped Contents-read token through
`OSS_MENU_TOKEN`, or `menuToken` in ignored `updater-private.properties`.
The menu token is separate from the APK updater and publishing credentials.
It is sent only to the configured repository's release API, never CDN redirects.
Do not commit secrets or include them in source archives.

The catalog format is illustrated by the generated `catalog.preview.json`.
`zip.assetId` is populated during publication for private API downloads.
The preview is a local preparation artifact, not evidence of publication.
`inventory.local.json` contains local paths and should not be committed.

## Validation

Installer unit tests cover dynamic versions, a catalog larger than one API page,
invalid paths/sizes/digests, corrupted downloads, and upgrading/downgrading data
whose hashes are absent from the APK. These are fixture tests, not Fortnite gameplay.
