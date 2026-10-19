import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from private_objects import Store

class PrivateObjectTests(unittest.TestCase):
    def test_chunked_object_is_complete_and_every_uploaded_part_is_hashed(self):
        class Fake(Store):
            def __init__(self):self.parts=[]
            def private(self):pass
            def asset(self,path,sha):
                data=path.read_bytes();self.parts.append(data)
                if hashlib.sha256(data).hexdigest()!=sha:raise ValueError('bad hash')
                return {'assetId':len(self.parts),'sha256':sha,'bytes':len(data)}
        with tempfile.TemporaryDirectory()as directory,patch('private_objects.CHUNK_BYTES',8):
            file=Path(directory)/'installer';file.write_bytes(b'complete installer bytes')
            store=Fake();location=store.upload(file)
            self.assertEqual(b''.join(store.parts),file.read_bytes())
            self.assertEqual(location['sizeBytes'],file.stat().st_size)
            self.assertEqual(location['sha256'],hashlib.sha256(file.read_bytes()).hexdigest())
            self.assertEqual(sum(p['bytes']for p in location['parts']),location['sizeBytes'])

    def test_public_repository_upload_is_refused(self):
        store=Store('test-token')
        with patch.object(store,'api',return_value={'private':False}):
            with self.assertRaisesRegex(ValueError,'visibility'):store.private()

if __name__=='__main__':unittest.main()
