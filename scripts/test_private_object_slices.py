import hashlib
from pathlib import Path
import tempfile,unittest
from unittest.mock import patch
from private_objects import Store,FileSlice

class PrivateObjectSliceTests(unittest.TestCase):
    def test_chunk_upload_reads_exact_bytes_without_temporary_copies(self):
        with tempfile.TemporaryDirectory()as directory:
            source=Path(directory)/'source.bin';source.write_bytes(b'abcdefghijk');uploaded=[]
            store=Store(token='test')
            def asset(view,digest):
                self.assertIsInstance(view,FileSlice)
                with view.open('rb')as stream:data=stream.read(2)+stream.read()+stream.read()
                self.assertEqual(hashlib.sha256(data).hexdigest(),digest);uploaded.append(data)
                self.assertEqual(list(Path(directory).iterdir()),[source])
                return {'assetId':len(uploaded),'bytes':len(data),'sha256':digest}
            with patch('private_objects.CHUNK_BYTES',4),patch.object(store,'private'),patch.object(store,'asset',side_effect=asset):
                result=store.upload(source)
            self.assertEqual(uploaded,[b'abcd',b'efgh',b'ijk'])
            self.assertEqual(result['sha256'],hashlib.sha256(b'abcdefghijk').hexdigest())
            self.assertEqual(result['sizeBytes'],11)

if __name__=='__main__':unittest.main()
