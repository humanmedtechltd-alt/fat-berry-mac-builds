import hashlib
from pathlib import Path
import tempfile
import unittest
from windows_payload import expected_payload,verify_payload

class WindowsPayloadTests(unittest.TestCase):
    def entry(self,path,data):return {'path':path,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}

    def test_complete_payload_and_resource_replacement_are_verified(self):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory);file=root/'{app}/Plugin.vst3/Contents/Resources/bank';file.parent.mkdir(parents=True);file.write_bytes(b'new-bank')
            product={'payloadFiles':[self.entry('{app}/Plugin.vst3/Contents/Resources/bank',b'old')],
                'replacements':[self.entry('{app}/Plugin.vst3/Contents/Resources/bank',b'new-bank')]}
            expected=expected_payload(product);verify_payload(root,expected)
            file.write_bytes(b'bad-bank')
            with self.assertRaisesRegex(ValueError,'hash differs'):verify_payload(root,expected)

    def test_undeclared_files_and_colliding_destinations_are_rejected(self):
        product={'payloadFiles':[self.entry('{app}/plugin',b'ok')]}
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory);(root/'{app}').mkdir();(root/'{app}/plugin').write_bytes(b'ok');(root/'{app}/unexpected').write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError,'undeclared'):verify_payload(root,expected_payload(product))
        product['additions']=[self.entry('{app}/plugin',b'ok')]
        with self.assertRaisesRegex(ValueError,'Duplicate'):expected_payload(product)
        product={'payloadFiles':[self.entry('../escape',b'bad')]}
        with self.assertRaisesRegex(ValueError,'Unsafe'):expected_payload(product)

if __name__=='__main__':unittest.main()
