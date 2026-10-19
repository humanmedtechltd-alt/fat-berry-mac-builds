import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from transfer import require_storage_reserve, PRODUCTION_DISK_RESERVE_BYTES


class ProductionReserveTests(unittest.TestCase):
    def test_large_output_stops_before_consuming_production_reserve(self):
        with patch('transfer.shutil.disk_usage', return_value=SimpleNamespace(free=PRODUCTION_DISK_RESERVE_BYTES + 10)):
            with self.assertRaises(ValueError):
                require_storage_reserve(Path('.'), 11)
            require_storage_reserve(Path('.'), 10)

    def test_status_can_report_the_blocked_upload_without_using_final_reserve(self):
        with patch('transfer.shutil.disk_usage', return_value=SimpleNamespace(free=1024**3)):
            require_storage_reserve(Path('.'), 1024, status=True)
            with self.assertRaises(ValueError):
                require_storage_reserve(Path('.'), 1024)
        with patch('transfer.shutil.disk_usage', return_value=SimpleNamespace(free=512 * 1024**2)):
            with self.assertRaises(ValueError):
                require_storage_reserve(Path('.'), 1024, status=True)


if __name__ == '__main__':
    unittest.main()
