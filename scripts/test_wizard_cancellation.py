import unittest
from test_windows_wizard import confirmed_cancellation


class CancellationTests(unittest.TestCase):
    def test_launcher_zero_requires_confirmed_cancel_without_installation(self):
        log='Exit Setup?\nUser chose Yes.\nDeinitializing Setup.'
        for code in [0, 2]:self.assertTrue(confirmed_cancellation(code,log))
        for code in [1, 5, -1073741819]:self.assertFalse(confirmed_cancellation(code,log))
        for invalid in ['',log.replace('User chose Yes.','User chose No.'),log+'\nInstallation process succeeded.']:
            self.assertFalse(confirmed_cancellation(0,invalid))


if __name__=='__main__':unittest.main()
