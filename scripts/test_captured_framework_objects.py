import json,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from public_sdk_inputs import restore_captured_framework_objects

class FrameworkObjectTests(unittest.TestCase):
    def test_only_exact_locked_public_sdk_objects_are_fetched_without_checkout(self):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory);(root/'third-party').mkdir()
            sources=[{'name':name,'repository_url':'https://github.com/example/'+name+'.git','revision':'a'*40,'destination':'vendor/'+name}
                     for name in ['iPlug2','vst3sdk','clap','clap-helpers']]
            file=root/'third-party/source-lock.json';file.write_text(json.dumps({'sources':sources}));calls=[]
            with patch('public_sdk_inputs.subprocess.check_output',return_value='a'*40):
                restore_captured_framework_objects(root,lambda *args:calls.append(args))
            self.assertEqual(len(calls),4)
            for call in calls:
                self.assertIn('--refetch',call);self.assertIn('--no-filter',call);self.assertNotIn('checkout',call);self.assertEqual(call[-1],'a'*40)
            sources[0]['repository_url']='D:/private/source';file.write_text(json.dumps({'sources':sources}))
            with self.assertRaisesRegex(ValueError,'exact public SDK pin'):
                restore_captured_framework_objects(root,lambda *args:self.fail('Unsafe fetch'))
    def test_a_different_pinned_commit_is_not_replaced(self):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory);(root/'third-party').mkdir()
            sources=[{'name':name,'repository_url':'https://github.com/example/'+name,'revision':'a'*40,'destination':'vendor/'+name}
                     for name in ['iPlug2','vst3sdk','clap','clap-helpers']]
            (root/'third-party/source-lock.json').write_text(json.dumps({'sources':sources}))
            with patch('public_sdk_inputs.subprocess.check_output',return_value='b'*40),self.assertRaisesRegex(ValueError,'history differs'):
                restore_captured_framework_objects(root,lambda *args:self.fail('Changed history'))

if __name__=='__main__':unittest.main()
