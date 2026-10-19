"""Restore immutable private content before verifying the complete source tree."""
import hashlib
import json
from pathlib import Path
import re

from public_sdk_inputs import contained


def restore(source, expected, entries, transfer):
    raw = (source / 'FATBERRY-SOURCE-SNAPSHOT.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Private content manifest differs from the frozen source')
    manifest = json.loads(raw)
    names = [entry['path'] for entry in entries]
    if len(set(names)) != len(names) or set(names) != set(manifest.get('detachedFiles', [])):
        raise ValueError('Private content inputs differ from the complete source manifest')
    records = {entry['path']: entry for entry in manifest['files']}
    for entry in entries:
        record = records[entry['path']]
        if (entry['sha256'] != record['sha256'] or entry['bytes'] != record['bytes']
                or not re.fullmatch(r'content-[a-f0-9]{64}\.[a-z0-9]+', entry['file'])):
            raise ValueError('Private content identity differs from the source record')
        destination = contained(source, entry['path'])
        if destination.exists():
            raise ValueError('Detached content destination already exists')
        destination.parent.mkdir(parents=True, exist_ok=True)
        if entry.get('storage'):
            from private_objects import Store
            Store().download(entry['storage'],destination)
        else:transfer('get', 'input/' + entry['file'], destination)
        with destination.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if destination.stat().st_size != record['bytes'] or digest != record['sha256']:
            raise ValueError('Private content transfer changed the required source bytes')
