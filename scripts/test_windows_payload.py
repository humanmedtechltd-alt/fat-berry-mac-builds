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

    def test_staged_bundle_checks_archive_bytes_at_the_real_install_destination(self):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory)
            installed='{app}/Plugin.vst3/Contents/x86_64-win/Plugin.vst3'
            archived='{tmp}/InstallStage/Plugin.vst3/Contents/x86_64-win/Plugin.vst3'
            entry={**self.entry(installed,b'plugin'),'archivePath':archived}
            file=root/archived;file.parent.mkdir(parents=True);file.write_bytes(b'plugin')
            expected=expected_payload({'payloadFiles':[entry]});verify_payload(root,expected)
            self.assertEqual(list(expected),[installed])
            extra=file.parent/'stale.dll';extra.write_bytes(b'stale')
            with self.assertRaisesRegex(ValueError,'undeclared'):verify_payload(root,expected)
            extra.unlink();file.write_bytes(b'broken')
            with self.assertRaisesRegex(ValueError,'hash differs'):verify_payload(root,expected)
            entry['archivePath']='../escape'
            with self.assertRaisesRegex(ValueError,'Unsafe'):expected_payload({'payloadFiles':[entry]})

    def test_staged_additions_are_written_into_the_bundle_before_atomic_promotion(self):
        from installer_script import write_script
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory);script=root/'installer.iss'
            archive='{tmp}/InstallStage/Plugin.vst3/Contents/Resources/NOTICE.txt'
            addition={**self.entry('{app}/Plugin.vst3/Contents/Resources/NOTICE.txt',b'notice'),
                'archivePath':archive}
            product={'name':'Plugin','version':'1.0.0','vst3Bundle':'Plugin.vst3','slug':'plugin',
                'setup':[],'minimumWindows':'10.0','fileEntries':[],'additions':[addition],
                'additionalSections':[],'installerCode':'[Code]\nprocedure Swap; begin end;',
                'runtimeImports':[]}
            write_script(product,{},root/'tooling',root/'design',root/'payload',root/'runtime.exe',root/'final.exe',script)
            result=script.read_text(encoding='utf-8-sig')
            self.assertIn('Source: "'+str(root/'payload'/archive)+'"',result)
            self.assertIn('DestDir: "'+str(Path(archive).parent)+'"',result)
            self.assertIn('procedure Swap;',result)

    def test_uninstall_removes_installed_factory_files_without_recursively_deleting_customer_presets(self):
        from installer_script import preserve_customer_directories
        section='[UninstallDelete]\nType: filesandordirs; Name: "{app}\\Plugin.vst3";\nType: filesandordirs; Name: "{commonappdata}\\VST3 Presets\\Vendor\\Plugin";\n'
        result=preserve_customer_directories(section,'Plugin.vst3')
        self.assertIn('Type: filesandordirs; Name: "{app}\\Plugin.vst3"',result)
        self.assertIn('Type: dirifempty; Name: "{commonappdata}\\VST3 Presets\\Vendor\\Plugin"',result)
        self.assertNotIn('filesandordirs; Name: "{commonappdata}',result)
        self.assertEqual(preserve_customer_directories(result,'Plugin.vst3'),result)

    def test_reinstall_preserves_customer_files_beside_shared_factory_presets(self):
        from installer_script import preserve_customer_directories
        section='[InstallDelete]\nType: filesandordirs; Name: "{commonappdata}\\VST3 Presets\\Vendor\\Product";\n'
        result=preserve_customer_directories(section,'Plugin.vst3')
        self.assertIn('Type: dirifempty;',result)
        self.assertNotIn('filesandordirs',result)

if __name__=='__main__':unittest.main()
