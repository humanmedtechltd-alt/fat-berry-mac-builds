import struct
import sys
import unittest
import io
import subprocess
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from macos_delivery_checks import macho_versions, wait_for_audio_component


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


class AudioComponentDiscoveryTests(unittest.TestCase):
    component = {'type': 'aufx', 'subtype': 'Ab12', 'manufacturer': 'Cd34'}

    def test_waits_for_exact_identity_without_running_validation(self):
        responses = [subprocess.CompletedProcess([], 0, 'aufx Ab12 Wrong : Other\n'),
                     subprocess.CompletedProcess([], 0, 'aufx Ab12 Cd34 : Product\n')]
        with patch('macos_delivery_checks.subprocess.run', side_effect=responses) as execute, \
                patch('time.sleep'):
            wait_for_audio_component(self.component, 'arm64', io.StringIO())
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(execute.call_args.args[0][-1], '-a')

    def test_missing_component_fails_without_validation(self):
        with patch('macos_delivery_checks.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '')):
            with self.assertRaises(TimeoutError):
                wait_for_audio_component(self.component, 'arm64', io.StringIO(), deadline_seconds=0)


if __name__ == '__main__':
    unittest.main()
