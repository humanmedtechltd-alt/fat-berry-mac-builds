import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import gzip

from private_file_inputs import restore


class PrivateContentTests(unittest.TestCase):
    def test_compressed_native_bank_restores_exact_source_bytes_and_rejects_changed_transport(self):
        for corrupt in [False,True]:
            with self.subTest(corrupt=corrupt),tempfile.TemporaryDirectory()as directory:
                root=Path(directory);data,entry,identity=self.fixture(root);compressed=gzip.compress(data,mtime=0)
                sha=hashlib.sha256(compressed).hexdigest();entry['file']='content-'+sha+'.gz'
                entry['archive']={'file':entry['file'],'bytes':len(compressed),'sha256':sha,'compression':'gzip'}
                def transfer(operation,remote,path):path.write_bytes(compressed+b'changed'if corrupt else compressed)
                if corrupt:
                    with self.assertRaisesRegex(ValueError,'transport changed'):restore(root,identity,[entry],transfer)
                else:
                    restore(root,identity,[entry],transfer);self.assertEqual((root/entry['path']).read_bytes(),data)
    def fixture(self, source):
        data = b'required-product-wavetable'
        entry = {'path': 'forge/out/content.fbw', 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        raw = json.dumps({'files': [entry], 'detachedFiles': [entry['path']]}).encode()
        (source / 'FATBERRY-SOURCE-SNAPSHOT.json').write_bytes(raw)
        return data, {**entry, 'file': 'content-' + entry['sha256'] + '.fbw'}, hashlib.sha256(raw).hexdigest()

    def test_exact_complete_content_restored_before_source_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data, entry, identity = self.fixture(root)
            def transfer(operation, remote, path):
                self.assertEqual((operation, remote), ('get', 'input/' + entry['file']))
                path.write_bytes(data)
            restore(root, identity, [entry], transfer)
            self.assertEqual((root / entry['path']).read_bytes(), data)

    def test_missing_tampered_and_escaped_content_fail(self):
        for change in ['missing', 'hash', 'path', 'transfer']:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                data, entry, identity = self.fixture(root)
                if change == 'hash': entry['sha256'] = '0' * 64
                if change == 'path': entry['path'] = '../secret'
                def transfer(operation, remote, path): path.write_bytes(b'corrupt' if change == 'transfer' else data)
                with self.assertRaises(ValueError):
                    restore(root, identity, [] if change == 'missing' else [entry], transfer)
