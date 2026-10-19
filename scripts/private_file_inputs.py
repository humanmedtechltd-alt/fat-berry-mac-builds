"""Restore immutable private content before verifying the complete source tree."""
import hashlib
import json
from pathlib import Path
import re
import gzip

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
        transport=entry.get('archive',entry)
        archive=entry.get('archive')
        target=destination.with_name(destination.name+'.compressed-input')if archive else destination
        if archive and (archive.get('compression')!='gzip'or archive.get('file')!=entry['file']):
            raise ValueError('Unsupported private content transport')
        if transport.get('storage'):
            from private_objects import Store
            Store().download(transport['storage'],target)
        else:transfer('get', 'input/' + entry['file'], target)
        if archive:
            try:
                with target.open('rb')as stream:compressed_sha=hashlib.file_digest(stream,'sha256').hexdigest()
                if target.stat().st_size!=archive['bytes']or compressed_sha!=archive['sha256']:
                    raise ValueError('Compressed private content transport changed')
                total=0
                with gzip.open(target,'rb')as compressed,destination.open('xb')as output:
                    while block:=compressed.read(1024**2):
                        total+=len(block)
                        if total>record['bytes']:raise ValueError('Compressed private content exceeds frozen source size')
                        output.write(block)
            finally:
                if target.exists():target.unlink()
        with destination.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if destination.stat().st_size != record['bytes'] or digest != record['sha256']:
            raise ValueError('Private content transfer changed the required source bytes')
