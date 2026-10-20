"""Resolve and verify the frozen factory files after actual installation."""
import hashlib
import os
from pathlib import Path, PurePosixPath


def verify(records, images, platform, *, program_data=None):
    if not records:
        return []
    if [p['name'] for p in records] != [p['name'] for p in images]:
        raise ValueError('Installed factory file coverage differs from the complete authored bank')
    root = Path('/') if platform == 'macos' else Path(program_data or os.environ['PROGRAMDATA'])
    verified = []
    for record in records:
        spelling = record['path']
        if platform == 'windows':
            if not spelling.startswith('{commonappdata}/'):
                raise ValueError('Factory preset has an unsupported Windows destination')
            spelling = spelling.removeprefix('{commonappdata}/')
        relative = PurePosixPath(spelling)
        if relative.is_absolute() or '..' in relative.parts or ':' in spelling or '\\' in spelling:
            raise ValueError('Factory preset escapes the installed content directory')
        path = root / spelling
        if path.stat().st_size != record['bytes']:
            raise ValueError('An actual installed factory preset has the wrong size')
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != record['sha256']:
            raise ValueError('An actual installed factory preset changed')
        verified.append({'name': record['name'], 'path': str(path)})
    return verified
