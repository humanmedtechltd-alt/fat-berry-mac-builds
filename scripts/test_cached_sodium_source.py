from pathlib import Path
import tempfile
import unittest
from cached_sodium_source import arguments


class CachedSodiumTests(unittest.TestCase):
    def test_verified_source_is_used_without_upstream_download(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'libsodium-source/libsodium-1.0.22'
            for name in ['configure','src/libsodium/include/sodium/version.h.in']:
                file=source/name; file.parent.mkdir(parents=True,exist_ok=True); file.write_text('frozen upstream source')
            self.assertEqual(arguments([{'directory':'libsodium-source'}],directory),
                ['-DFETCHCONTENT_SOURCE_DIR_FATBERRY_SODIUM_SRC='+str(source)])
            (source/'configure').unlink()
            with self.assertRaisesRegex(ValueError,'cache is incomplete'):
                arguments([{'directory':'libsodium-source'}],directory)

    def test_retained_legacy_inputs_remain_usable(self):
        self.assertEqual(arguments([],Path('work')),[])


if __name__=='__main__':unittest.main()
