import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from reconcile import attach_publication_result


class PublicationReportingTests(unittest.TestCase):
    def test_pending_candidate_keeps_live_proof_without_becoming_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'publication-status.json').write_text(json.dumps({'products': {'example': {
                'status': 'published', 'installers': {'windows': 'a' * 64, 'macos': 'b' * 64}}}}))
            pending = {'slug': 'example', 'platform': 'windows', 'status': 'queued'}
            result = attach_publication_result(root, pending)
            self.assertEqual(result['status'], 'queued')
            self.assertEqual(result['publishedInstallerSha256'], 'a' * 64)
            current = {'slug': 'example', 'platform': 'windows', 'status': 'candidate', 'sha256': 'a' * 64}
            self.assertEqual(attach_publication_result(root, current)['status'], 'published')
            different = {'slug': 'example', 'platform': 'windows', 'status': 'candidate', 'sha256': 'c' * 64}
            self.assertEqual(attach_publication_result(root, different)['status'], 'candidate')


if __name__ == '__main__': unittest.main()
