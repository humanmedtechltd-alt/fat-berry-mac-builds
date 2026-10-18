import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from macos_delivery_checks import macho_versions


def thin(cpu, minimum):
    command = struct.pack('<IIIIII', 0x32, 24, 1, minimum, 0, 0)
    return struct.pack('<IIIIIIII', 0xfeedfacf, cpu, 0, 6, 1, len(command), 0, 0) + command


class MachOVersionTests(unittest.TestCase):
    def test_universal_distinct_deployment_targets(self):
        arm = thin(0x0100000c, 0x0b0000)
        intel = thin(0x01000007, 0x0a0f00)
        start = 48
        fat = struct.pack('>II', 0xcafebabe, 2)
        fat += struct.pack('>IIIII', 0x0100000c, 0, start, len(arm), 0)
        fat += struct.pack('>IIIII', 0x01000007, 0, start + len(arm), len(intel), 0)
        self.assertEqual(macho_versions(fat + arm + intel), [
            {'architecture': 'arm64', 'minimumOS': '11.0.0'},
            {'architecture': 'x86_64', 'minimumOS': '10.15.0'}])

    def test_truncated_and_foreign_platform_rejected(self):
        for data in (thin(0x0100000c, 0x0b0000)[:-1],
                     thin(0x0100000c, 0x0b0000)[:40] + struct.pack('<I', 2) + thin(0x0100000c, 0x0b0000)[44:]):
            with self.assertRaises(ValueError):
                macho_versions(data)


if __name__ == '__main__':
    unittest.main()
