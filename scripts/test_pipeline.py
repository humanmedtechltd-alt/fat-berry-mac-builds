"""Offline regression checks for release transport and result isolation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from reconcile import inspect, inspect_windows, status_path, attach_production_result
from compilation_identity import fingerprint


class ReleasePipelineTests(unittest.TestCase):
    def test_activation_failure_tracks_exact_installer_and_latest_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for run, sha, state in [(10, 'current', 'failed'), (11, 'old-installer', 'passed')]:
                folder = root / f'output/diagnostics/{run}/activation/macos'
                folder.mkdir(parents=True)
                (folder / 'status.json').write_text(json.dumps({'slug': 'big', 'installerSha256': sha, 'status': state}))
            row = {'slug': 'big', 'platform': 'macos', 'status': 'candidate-verified', 'sha256': 'current'}
            self.assertEqual(attach_production_result(root, dict(row))['status'], 'acceptance-failed')
            folder = root / 'output/diagnostics/12/activation/macos'
            folder.mkdir(parents=True)
            (folder / 'status.json').write_text(json.dumps({'slug': 'big', 'installerSha256': 'current', 'status': 'passed'}))
            self.assertEqual(attach_production_result(root, row)['stage'], 'publication-gate-pending')
    def test_windows_report_selects_exact_packaging_input_and_fresh_module(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'output/runs/10/1/windows/big'
            folder.mkdir(parents=True)
            payload = b'fresh-installer'
            (folder / 'installer-win.exe').write_bytes(payload)
            identity = {field: field for field in ('slug', 'productId', 'version', 'keygenProductId', 'keygenPolicyId', 'licensingClientCommit')}
            identity['slug'] = 'big'
            (folder / 'status.json').write_text(json.dumps({'status': 'candidate', 'buildInputSha256': 'source', 'inputIndexSha256': 'new-package'}))
            (folder / 'release.json').write_text(json.dumps({**identity, 'vst3Sha256': 'new-module',
                'auditedBuildInputSha256': 'source', 'sizeBytes': len(payload),
                'sha256': hashlib.sha256(payload).hexdigest()}))
            product = {'slug': 'big', 'name': 'BIG', 'buildInputSha256': 'source',
                       'original': {**identity, 'vst3Sha256': 'old-module'},
                       'compiled': {'moduleSha256': 'new-module'}, 'inputIndexSha256': 'new-package'}
            self.assertEqual(inspect_windows(root, product)['status'], 'candidate-verified')
            product['inputIndexSha256'] = 'different-package'
            self.assertEqual(inspect_windows(root, product)['status'], 'queued')

    def test_continuation_is_claimed_only_once(self):
        for kind in ("activation", "build"):
            with self.subTest(kind=kind):
                self.check_continuation_claim(kind)

    def check_continuation_claim(self, kind):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "input").mkdir()
            name = kind + "-continuation-" + "a" * 64 + ".json"
            (root / "input" / name).write_text('{"queued":true}')
            args = [sys.executable, str(Path(__file__).with_name("transfer.py")), str(root)]
            env = {**os.environ, "SSH_ORIGINAL_COMMAND": "get input/" + name}
            first = subprocess.run(args, env=env, capture_output=True)
            second = subprocess.run(args, env=env, capture_output=True)
            self.assertEqual(first.returncode, 0)
            self.assertEqual(first.stdout, b'{"queued":true}')
            self.assertNotEqual(second.returncode, 0)
    def test_compile_cache_ignores_packaging_but_tracks_licensing(self):
        product = {key: key for key in ("slug", "version", "sourceSnapshot", "framework", "minimumOS", "vst3Target",
                   "auTarget", "vst3Bundle", "auBundle", "manufacturerCode", "pluginCode", "auType", "cmakeArguments",
                   "keygenProductId", "keygenPolicyId", "licensingClientCommit")}
        index = {"dependencies": [{"directory": "fat-berry-release-tooling", "sha256": "package1"},
                                  {"directory": "fat-berry-licensing-client", "sha256": "licence1"}],
                 **{key: key for key in ("toolingCompileSha256", "sodiumSha256", "keygenPublicKey", "licensingApiOrigin")}}
        original = fingerprint(index, product)
        index["dependencies"][0]["sha256"] = "package2"
        self.assertEqual(fingerprint(index, product), original)
        index["dependencies"][1]["sha256"] = "licence2"
        self.assertNotEqual(fingerprint(index, product), original)

    def test_stale_dependency_success_cannot_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "output/runs/10/1/macos/big/status.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"sourceSnapshot": "same", "compilationFingerprint": "old", "status": "passed"}))
            row = inspect(root, {"slug": "big", "name": "BIG", "sourceSnapshot": "same", "compilationFingerprint": "new"})
            self.assertEqual(row["stage"], "awaiting-current-dependencies")

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
