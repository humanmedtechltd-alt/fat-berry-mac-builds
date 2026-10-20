import hashlib
from pathlib import Path
import tempfile
import unittest
from installed_factory_presets import verify


class InstalledFactoryPresetTests(unittest.TestCase):
    def test_actual_installed_bank_and_changed_missing_or_omitted_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'VST3 Presets/Bank Ω/First.vstpreset'
            path.parent.mkdir(parents=True)
            path.write_bytes(b'actual-installed-preset')
            records = [{'name': 'First', 'path': '{commonappdata}/' + path.relative_to(root).as_posix(),
                        'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]
            images = [{'name': 'First'}]
            self.assertEqual(verify(records, images, 'windows', program_data=root),
                             [{'name': 'First', 'path': str(path)}])
            with self.assertRaisesRegex(ValueError, 'coverage'):
                verify(records, images + [{'name': 'Omitted'}], 'windows', program_data=root)
            path.write_bytes(b'changed-installed-data')
            with self.assertRaises(ValueError):
                verify(records, images, 'windows', program_data=root)
            path.unlink()
            with self.assertRaises(FileNotFoundError):
                verify(records, images, 'windows', program_data=root)

    def test_preset_destination_cannot_escape_program_data(self):
        records = [{'name': 'First', 'path': '{commonappdata}/../outside.vstpreset'}]
        with self.assertRaisesRegex(ValueError, 'escapes'):
            verify(records, [{'name': 'First'}], 'windows', program_data=Path('.'))


if __name__ == '__main__':
    unittest.main()
