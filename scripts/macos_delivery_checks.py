"""Inspect every installed Mach-O slice and exercise the delivered recovery script."""
import plistlib
import shlex
import struct
import subprocess
from pathlib import Path


def version(value):
    return (value >> 16, (value >> 8) & 255, value & 255)


def macho_versions(data):
    """Return architecture/minimum OS for thin or fat Mach-O, without executing it."""
    fat = {b'\xca\xfe\xba\xbe': ('>', False), b'\xbe\xba\xfe\xca': ('<', False),
           b'\xca\xfe\xba\xbf': ('>', True), b'\xbf\xba\xfe\xca': ('<', True)}
    thin = {b'\xcf\xfa\xed\xfe': ('<', 32), b'\xfe\xed\xfa\xcf': ('>', 32),
            b'\xce\xfa\xed\xfe': ('<', 28), b'\xfe\xed\xfa\xce': ('>', 28)}
    if data[:4] in fat:
        endian, wide = fat[data[:4]]
        count = struct.unpack_from(endian + 'I', data, 4)[0]
        if not 1 <= count <= 16:
            raise ValueError('Invalid universal architecture count')
        result = []
        stride = 32 if wide else 20
        for index in range(count):
            record = 8 + index * stride
            offset, size = struct.unpack_from(endian + ('QQ' if wide else 'II'), data, record + 8)
            if offset + size > len(data):
                raise ValueError('Truncated universal Mach-O')
            result.extend(macho_versions(data[offset:offset + size]))
        return result
    if data[:4] not in thin:
        return []
    endian, header = thin[data[:4]]
    cpu = struct.unpack_from(endian + 'I', data, 4)[0]
    architecture = {0x01000007: 'x86_64', 0x0100000c: 'arm64'}.get(cpu)
    if not architecture:
        raise ValueError('Unexpected Mac binary architecture')
    count, command_bytes = struct.unpack_from(endian + 'II', data, 16)
    end = header + command_bytes
    if end > len(data):
        raise ValueError('Truncated Mach-O load commands')
    cursor = header
    minimum = None
    for _ in range(count):
        command, size = struct.unpack_from(endian + 'II', data, cursor)
        if size < 8 or cursor + size > end:
            raise ValueError('Invalid Mach-O load command')
        if command == 0x24:
            minimum = version(struct.unpack_from(endian + 'I', data, cursor + 8)[0])
        elif command == 0x32:
            platform, encoded = struct.unpack_from(endian + 'II', data, cursor + 8)
            if platform != 1:
                raise ValueError('Embedded binary targets a different Apple platform')
            minimum = version(encoded)
        cursor += size
    if minimum is None:
        raise ValueError('Mach-O has no minimum macOS load command')
    return [{'architecture': architecture, 'minimumOS': '.'.join(map(str, minimum))}]


def inspect_bundles(bundles, minimum):
    target = tuple(map(int, minimum.split('.')))
    target += (0,) * (3 - len(target))
    records = []
    for bundle in bundles:
        for path in sorted(bundle.rglob('*')):
            if not path.is_file() or path.is_symlink():
                continue
            with path.open('rb') as stream:
                magic = stream.read(4)
            if magic not in {b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xce',
                             b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}:
                continue
            slices = macho_versions(path.read_bytes())
            if {item['architecture'] for item in slices} != {'arm64', 'x86_64'} or len(slices) != 2:
                raise ValueError('A nested runtime binary is not universal: ' + path.name)
            if any(tuple(map(int, item['minimumOS'].split('.'))) > target for item in slices):
                raise ValueError('A nested runtime binary exceeds the declared minimum OS: ' + path.name)
            dependencies = subprocess.run(['otool', '-L', str(path)], check=True, capture_output=True,
                                          text=True, timeout=30).stdout
            for line in dependencies.splitlines():
                if not line[:1].isspace() or 'compatibility version' not in line:
                    continue
                dependency = line.strip().split(' (compatibility version', 1)[0]
                if dependency.startswith('/') and not dependency.startswith(('/System/Library/', '/usr/lib/')):
                    raise ValueError('Nested binary depends on a build-machine library')
                if dependency.startswith('@loader_path/') and not (path.parent / dependency[13:]).resolve().is_file():
                    raise ValueError('Nested binary has an unresolved loader-relative dependency')
            records.append({'path': str(path), 'slices': slices})
    if len(records) < len(bundles):
        raise ValueError('No runtime binary found in a plugin bundle')
    return records


def exercise_recovery(mount, bundles, run):
    recovery = mount / 'Approve installed Fat Berry plugins.command'
    expected = '#!/bin/sh\nset -eu\n' + '\n'.join(
        'sudo /usr/bin/xattr -dr com.apple.quarantine ' + shlex.quote(str(bundle))
        + ' 2>/dev/null || true' for bundle in bundles) + '\n'
    if recovery.read_text() != expected:
        raise ValueError('Delivered quarantine recovery does not match the exact product paths')
    readme = (mount / 'Read Me - Installation.txt').read_text()
    if 'Privacy & Security > Open Anyway' not in readme or 'not Apple-notarized' not in readme:
        raise ValueError('The delivered installer omits its approval instructions')
    for bundle in bundles:
        run(['sudo', '/usr/bin/xattr', '-r', '-w', 'com.apple.quarantine',
             '0083;00000000;FatBerryReleaseAcceptance;', str(bundle)])
        check = subprocess.run(['/usr/bin/xattr', '-p', 'com.apple.quarantine', str(bundle)],
                               capture_output=True, timeout=30)
        if check.returncode:
            raise ValueError('The quarantine test did not establish its precondition')
    run(['/bin/sh', str(recovery)])
    for bundle in bundles:
        attributes = subprocess.run(['/usr/bin/xattr', '-lr', str(bundle)], check=True,
                                    capture_output=True, timeout=30).stdout
        if b'com.apple.quarantine' in attributes:
            raise ValueError('The delivered recovery script left quarantine attributes')
        run(['codesign', '--verify', '--deep', '--strict', str(bundle)])
    return {'quarantineRecovery': 'passed-delivered-product-specific-script',
            'approvalInstructions': 'present-in-delivered-dmg',
            'openAnywayGUI': 'not-automated'}
