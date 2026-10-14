"""Offline regression checks for release transport and result isolation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from reconcile import inspect, status_path


class ReleasePipelineTests(unittest.TestCase):
    def test_transfer_rejects_path_escape_and_shell_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "input").mkdir()
            (root / "output").mkdir()
            for command in ("get ../secret", "get output/private", "put input/index.json", "get /etc/passwd", "sh -c id"):
                result = subprocess.run([sys.executable, str(Path(__file__).with_name("transfer.py")), str(root)],
                                        env={**os.environ, "SSH_ORIGINAL_COMMAND": command}, capture_output=True)
                self.assertNotEqual(result.returncode, 0, command)

    def test_latest_attempt_is_selected_numerically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for run, attempt in ((9, 1), (10, 1), (10, 2)):
                path = root / f"output/runs/{run}/{attempt}/macos/big/status.json"
                path.parent.mkdir(parents=True)
                path.write_text("{}")
            self.assertEqual(status_path(root, "macos", "big"), root / "output/runs/10/2/macos/big/status.json")

    def test_stale_source_success_cannot_release_new_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "output/runs/10/1/macos/big/status.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"sourceSnapshot": "old", "status": "passed"}))
            row = inspect(root, {"slug": "big", "name": "BIG", "sourceSnapshot": "new"})
            self.assertEqual(row["status"], "queued")
            self.assertEqual(row["stage"], "awaiting-current-snapshot")

    def test_success_without_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "output/runs/10/1/macos/big/status.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"sourceSnapshot": "same", "status": "passed"}))
            with self.assertRaisesRegex(ValueError, "release record"):
                inspect(root, {"slug": "big", "name": "BIG", "sourceSnapshot": "same"})

    def test_status_upload_is_atomic_and_triggers_event(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "output").mkdir()
            data = b'{"status":"failed"}\n'
            subprocess.run([sys.executable, str(Path(__file__).with_name("transfer.py")), str(root)],
                           env={**os.environ, "SSH_ORIGINAL_COMMAND": "put output/runs/1/1/macos/big/status.json"},
                           input=data, check=True)
            self.assertEqual((root / "output/runs/1/1/macos/big/status.json").read_bytes(), data)
            self.assertEqual(len((root / "events.log").read_text().splitlines()), 1)
            self.assertEqual(list(root.rglob("*.part-*")), [])


if __name__ == "__main__":
    unittest.main()
