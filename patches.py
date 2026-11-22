"""Build small OSS block patches and a shared exact-original restore payload.

Every patch includes the union of changed blocks across the supplied menu inventory,
so a newer menu can be downgraded without retaining its extra modifications.
Original inputs and historical ZIP releases are read-only.
"""
import argparse
import concurrent.futures
import gzip
import hashlib
import json
from pathlib import Path
import struct
import zipfile
import provider

BLOCK = 65536
MAX_PATCH = 8 * 1024 * 1024


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(sizes, blocks):
    data = bytearray(struct.pack('>ii', 0x4f535331, len(sizes)))
    for size, entries in zip(sizes, blocks):
        data.extend(struct.pack('>qi', size, len(entries)))
        for offset, value in sorted(entries.items()):
            assert offset % BLOCK == 0 and len(value) == min(BLOCK, size - offset)
            data.extend(struct.pack('>qi', offset, len(value)))
            data.extend(value)
    compressed = gzip.compress(data, mtime=0)
    if len(compressed) > MAX_PATCH:
        raise ValueError(f'Patch exceeds the small-download limit: {len(compressed)}')
    return compressed


def prepare(inventory, originals, original_specs, compatibility, repository, output):
    output.mkdir(parents=True, exist_ok=True)
    bases = [(originals / name).read_bytes() for name in provider.NAMES]
    for base, spec in zip(bases, original_specs):
        assert len(base) == spec['bytes'] and sha(base) == spec['sha256'], 'Original input differs'
    union = [set(compatibility.get(name, [])) for name in provider.NAMES]
    snapshots = []
    for record in inventory:
        path, menu = Path(record['path']), record['menu']
        with path.open('rb') as stream:
            assert path.stat().st_size == menu['zip']['bytes'] and provider.digest(stream) == menu['zip']['sha256']
        diffs, hashes = [], []
        with zipfile.ZipFile(path) as archive:
            assert len(archive.namelist()) == 3 and set(archive.namelist()) == set(provider.NAMES)
            for i, spec in enumerate(menu['files']):
                assert spec['name'] == provider.NAMES[i]
                changed, block_hashes, digest, offset = {}, [], hashlib.sha256(), 0
                with archive.open(spec['name']) as stream:
                    while block := stream.read(BLOCK):
                        digest.update(block); block_hashes.append(sha(block))
                        if block != bases[i][offset:offset + len(block)]:
                            changed[offset] = block; union[i].add(offset)
                        offset += len(block)
                assert offset == spec['bytes'] and digest.hexdigest() == spec['sha256']
                diffs.append(changed); hashes.append(block_hashes)
        snapshots.append((menu, diffs, hashes))
        print(f"Scanned menu {menu['version']}", flush=True)
    # Restore uses the same union and is shared by every target, downloaded only once.
    restore_blocks = [{at: base[at:at + BLOCK] for at in offsets if at < len(base)} for base, offsets in zip(bases, union)]
    restore_data = encode([len(b) for b in bases], restore_blocks)
    restore_hash = sha(restore_data)
    restore_name = f'originals-{restore_hash}.ossdelta'
    (output / restore_name).write_bytes(restore_data)
    restore = {'format': 'oss-blocks-v1', 'bytes': len(restore_data), 'sha256': restore_hash,
               'url': f'https://github.com/{repository}/releases/download/restore-v1-{restore_hash}/{restore_name}'}
    prepared = []
    for menu, diffs, target_hashes in snapshots:
        blocks = []
        for i, spec in enumerate(menu['files']):
            entries = {at: diffs[i].get(at, bases[i][at:at + min(BLOCK, spec['bytes'] - at)])
                       for at in union[i] if at < spec['bytes']}
            blocks.append(entries)
            # Verify complete target bytes, not just the payload hash.
            digest = hashlib.sha256()
            for at in range(0, spec['bytes'], BLOCK):
                digest.update(entries.get(at, bases[i][at:at + min(BLOCK, spec['bytes'] - at)]))
            assert digest.hexdigest() == spec['sha256']
            # Every historical source produces the exact target block sequence.
            for source, _, source_hashes in snapshots:
                actual = list(source_hashes[i][:len(target_hashes[i])])
                actual += [''] * (len(target_hashes[i]) - len(actual))
                for at, block in entries.items():
                    actual[at // BLOCK] = sha(block)
                assert actual == target_hashes[i], f"Cannot patch {source['version']} to {menu['version']}"
        data = encode([f['bytes'] for f in menu['files']], blocks)
        digest = sha(data); name = f"menu_v{menu['version']}-{digest}.ossdelta"
        (output / name).write_bytes(data)
        item = dict(menu)
        item['patch'] = {'format': 'oss-blocks-v1', 'bytes': len(data), 'sha256': digest,
                         'url': f"https://github.com/{repository}/releases/download/menu-v{menu['version']}/{name}"}
        item['restore'] = restore
        prepared.append(item)
        print(f"Prepared menu {menu['version']}: {len(data):,} bytes", flush=True)
    # Verify restoring all known source block sequences, including truncated targets.
    for i, base in enumerate(bases):
        expected = [sha(base[at:at + BLOCK]) for at in range(0, len(base), BLOCK)]
        for menu, _, hashes in snapshots:
            actual = hashes[i][:len(expected)] + [''] * max(0, len(expected) - len(hashes[i]))
            for at, block in restore_blocks[i].items(): actual[at // BLOCK] = sha(block)
            assert actual == expected, f"Cannot restore {menu['version']}"
    catalog = {'schema': 1, 'menus': prepared}
    (output / 'catalog.preview.json').write_bytes((json.dumps(catalog, indent=2) + '\n').encode())
    evidence = {'menus': len(prepared), 'crossVersionPairsVerified': len(prepared) ** 2,
                'allTargetFileHashesVerified': True, 'allOriginalRestoresVerified': True,
                'maximumPatchBytes': max(m['patch']['bytes'] for m in prepared), 'sharedRestoreBytes': len(restore_data),
                'originals': original_specs, 'blocks': {name: sorted(offsets) for name, offsets in zip(provider.NAMES, union)}}
    (output / 'patch-verification.json').write_text(json.dumps(evidence, indent=2) + '\n')
    return catalog


def publish(directory, repository):
    catalog = json.loads((directory / 'catalog.preview.json').read_text())
    releases = {r['tag_name']: r for page in provider.gh('api', f'repos/{repository}/releases?per_page=100', '--paginate', '--slurp', json_result=True) for r in page}
    artifacts = {m[k]['url']: m[k] for m in catalog['menus'] for k in ('patch', 'restore')}
    def upload(item):
        tag, name = item['url'].split('/releases/download/')[1].split('/')
        path = directory / name
        assert path.stat().st_size == item['bytes'] and sha(path.read_bytes()) == item['sha256']
        release = releases.get(tag)
        if release is None:
            provider.gh('release', 'create', tag, '--repo', repository, '--draft', '--title', 'Original restore patch', '--notes', 'Verified shared original-byte blocks.')
            release = provider.release_by_tag(repository, tag)
        asset = next((a for a in release['assets'] if a['name'] == name), None)
        if asset is None:
            provider.gh('release', 'upload', tag, str(path), '--repo', repository)
            release = provider.gh('api', f"repos/{repository}/releases/{release['id']}", json_result=True)
            asset = next(a for a in release['assets'] if a['name'] == name)
        assert asset['size'] == item['bytes'] and asset.get('digest') == 'sha256:' + item['sha256']
        if release['draft']: provider.gh('release', 'edit', tag, '--repo', repository, '--draft=false', '--latest=false')
        item['assetId'] = asset['id']
        print(f'Published {name}', flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(upload, artifacts.values()))
    for menu in catalog['menus']:
        for key in ('patch', 'restore'): menu[key] = artifacts[menu[key]['url']]
    # Merge with the current latest catalog immediately before publication.
    latest = provider.gh('api', f'repos/{repository}/releases/latest', json_result=True)
    asset = next(a for a in latest['assets'] if a['name'] == 'catalog.json')
    current = json.loads(provider.gh('api', f"repos/{repository}/releases/assets/{asset['id']}", '-H', 'Accept: application/octet-stream'))
    indexed = {m['version']: m for m in current['menus']}
    for menu in catalog['menus']:
        old = indexed[menu['version']]
        assert old['files'] == menu['files'] and old['zip']['sha256'] == menu['zip']['sha256']
        indexed[menu['version']] = dict(old, patch=menu['patch'], restore=menu['restore'])
    provider.publish_catalog({'schema': 1, 'menus': sorted(indexed.values(), key=lambda m: tuple(map(int, m['version'].split('.'))), reverse=True)}, repository, directory)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['prepare', 'publish']); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--repository', default='oss-stw/OSS-Android-Data-Provider')
    p.add_argument('--inventory', type=Path); p.add_argument('--originals', type=Path); p.add_argument('--manifest', type=Path); p.add_argument('--compatibility', type=Path)
    args = p.parse_args()
    if args.command == 'publish': publish(args.output, args.repository)
    else:
        manifest = json.loads(args.manifest.read_text())
        prepare(json.loads(args.inventory.read_text()), args.originals, manifest['restore']['files'], json.loads(args.compatibility.read_text())['files'], args.repository, args.output)
