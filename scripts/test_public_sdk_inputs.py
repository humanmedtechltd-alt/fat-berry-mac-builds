import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from public_sdk_inputs import contained, restore


class PublicSdkTests(unittest.TestCase):
    def test_rejects_manifest_replacement_before_any_git_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            raw = json.dumps({"externalInputs": []}).encode()
            (source / "FATBERRY-SOURCE-SNAPSHOT.json").write_bytes(raw)
            commands = []
            with self.assertRaisesRegex(ValueError, "frozen product snapshot"):
                restore(source, "0" * 64, lambda *args: commands.append(args))
            self.assertEqual(commands, [])
            restore(source, hashlib.sha256(raw).hexdigest(), lambda *args: commands.append(args))

    def test_sdk_paths_cannot_replace_credentials_or_escape_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("../outside", ".git/config", "vendor/../../outside", ".private/key", "/etc/passwd"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    contained(root, name)


if __name__ == "__main__":
    unittest.main()
