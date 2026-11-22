import gzip
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile
import patches
import provider


def apply(data, source):
    stream = io.BytesIO(gzip.decompress(data))
    assert struct.unpack('>ii', stream.read(8)) == (0x4f535331, 3)
    result = []
    for original in source:
        size, count = struct.unpack('>qi', stream.read(12))
        value = bytearray(original[:size]); value.extend(b'\0' * (size - len(value)))
        for _ in range(count):
            at, length = struct.unpack('>qi', stream.read(12))
            value[at:at + length] = stream.read(length)
        result.append(bytes(value))
    assert not stream.read(1)
    return result


class PatchTests(unittest.TestCase):
    def test_union_patches_switch_both_directions_and_restore_exact_originals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); original = root / 'originals'; original.mkdir()
            repo = 'oss-stw/OSS-Android-Data-Provider'
            base = [bytes([i + 1]) * (2 * 65536 + 19) for i in range(3)]
            specs = []
            for name, data in zip(provider.NAMES, base):
                (original / name).write_bytes(data)
                specs.append({'name': name, 'bytes': len(data), 'sha256': patches.sha(data)})
            targets = [base, [b'changed' + x[7:] for x in base], [x[:70000] + b'new' + x[70003:] for x in base]]
            inventory = []
            for i, target in enumerate(targets):
                path = root / f'menu_v9.{i}.zip'
                with zipfile.ZipFile(path, 'w') as archive:
                    for name, data in zip(provider.NAMES, target): archive.writestr(name, data)
                inventory.append({'path': str(path), 'menu': provider.inspect(path, repo)})
            output = root / 'patches'
            catalog = patches.prepare(inventory, original, specs, {}, repo, output)
            for menu, target in zip(catalog['menus'], targets):
                delta = (output / menu['patch']['url'].split('/')[-1]).read_bytes()
                for source in targets: self.assertEqual(target, apply(delta, source))
                restore = (output / menu['restore']['url'].split('/')[-1]).read_bytes()
                self.assertEqual(base, apply(restore, target))
            self.assertEqual(9, json.loads((output / 'patch-verification.json').read_text())['crossVersionPairsVerified'])
            self.assertEqual(base, [(original / name).read_bytes() for name in provider.NAMES])

    def test_wrong_originals_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in provider.NAMES: (root / name).write_bytes(b'wrong')
            specs = [{'bytes': 5, 'sha256': '0' * 64} for _ in provider.NAMES]
            with self.assertRaises(AssertionError): patches.prepare([], root, specs, {}, 'oss-stw/test', root / 'out')


if __name__ == '__main__': unittest.main()
