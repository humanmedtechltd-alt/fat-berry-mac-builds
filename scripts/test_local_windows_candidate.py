import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

with patch.dict(os.environ,{'RUNNER_SSH_HOST':'example.invalid'}):
    import accept_local_windows_candidate as module
from windows_payload import expected_payload

class LocalCandidateAcceptanceTests(unittest.TestCase):
    def run_candidate(self,tamper=False):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory);source=root/'input.exe';source.write_bytes(b'packaged-installer')
            digest=lambda value:hashlib.sha256(value).hexdigest()
            entry={'path':'{app}/Plugin.vst3/Contents/module','bytes':6,'sha256':digest(b'plugin')}
            product={'slug':'product','payloadFiles':[entry],'runtimeImports':[],'needsRepackage':True,
                'modulePath':entry['path'],'buildInputSha256':'source',
                'original':{'vst3Sha256':entry['sha256'],'sourceCommit':'commit','sourceSnapshot':'snapshot'},
                'localCandidate':{'file':'candidate.exe','bytes':len(source.read_bytes()),'sha256':digest(source.read_bytes()),
                    'packagingFingerprint':'package-fingerprint','payloadFiles':[entry],
                    'validation':{'archiveIntegrity':'passed','payloadHashes':'passed','microsoftRuntimeSignatures':'passed'}}}
            validator=root/'pluginval/pluginval.exe';validator.parent.mkdir();validator.write_bytes(b'validator')
            def fetch(entry,destination):
                target=destination/entry['file'];shutil.copyfile(source,target);return target
            def extract(arguments,**kwargs):
                if '-x'in arguments:
                    file=root/'verified'/entry['path'];file.parent.mkdir(parents=True);file.write_bytes(b'tamper'if tamper else b'plugin')
            stages=[];uploaded=[];installed=[]
            fake_install=SimpleNamespace(validate_install=lambda p,f,*args:(installed.append(f.read_bytes())or {'cleanInstall':'passed'}))
            fake_wizard=SimpleNamespace(validate_wizard=lambda *args:{'wizard':'passed'})
            with patch.object(module,'input_object',side_effect=fetch),patch.object(module.subprocess,'run',side_effect=extract),\
                 patch.object(module,'transfer',side_effect=lambda operation,path,file:uploaded.append((path,file.read_bytes()))),\
                 patch.object(module,'output_prefix',return_value='output/run/windows/product'),\
                 patch.dict(sys.modules,{'test_windows_install':fake_install,'test_windows_wizard':fake_wizard}),\
                 patch.dict(os.environ,{'INPUT_INDEX_SHA256':'index'}), (root/'log').open('w')as log:
                if tamper:
                    with self.assertRaisesRegex(ValueError,'hash differs'):module.accept(product,{},root,log,lambda *s:stages.append(s))
                    self.assertEqual(installed,[]);self.assertFalse(any(s[-1]=='candidate'for s in stages))
                else:
                    module.accept(product,{},root,log,lambda *s:stages.append(s))
                    self.assertEqual(installed,[source.read_bytes()]);self.assertEqual(stages[-1],('release-acceptance','candidate'))
                    release=json.loads((root/'release/release.json').read_text())
                    self.assertEqual(release['sha256'],product['localCandidate']['sha256'])
                    self.assertEqual(release['payloadFiles'],list(expected_payload(product).values()))
                    self.assertEqual(release['validation']['activation'],'pending')

    def test_prepackaged_exact_bytes_still_require_clean_runner_installation(self):self.run_candidate()
    def test_tampered_extracted_file_is_refused_before_installation(self):self.run_candidate(True)

if __name__=='__main__':unittest.main()
