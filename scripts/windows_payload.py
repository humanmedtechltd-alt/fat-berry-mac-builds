"""The same complete payload inventory is used before and after packaging."""
from pathlib import Path,PurePosixPath
import hashlib

def archive_path(entry):
    relative=entry.get('archivePath',entry['path']).replace('\\','/')
    path=PurePosixPath(relative)
    if path.is_absolute()or '..'in path.parts or ':'in relative or not path.parts:
        raise ValueError('Unsafe archive payload path')
    return relative

def expected_payload(product):
    expected={}
    for collection,replace in [('payloadFiles',False),('replacements',True),('additions',False)]:
        for entry in product.get(collection,[]):
            relative=entry['path'].replace('\\','/');path=PurePosixPath(relative)
            if path.is_absolute()or '..'in path.parts or ':'in relative or not path.parts:
                raise ValueError('Unsafe installed payload path')
            if replace and relative not in expected:raise ValueError('Replacement has no original destination')
            if not replace and relative in expected:raise ValueError('Duplicate installed payload destination')
            expected[relative]=entry
    if not expected:raise ValueError('Empty installed payload inventory')
    archived=[archive_path(e)for e in expected.values()]
    if len(archived)!=len(set(archived)):raise ValueError('Duplicate archive payload destination')
    return expected

def verify_payload(directory,expected):
    directory=Path(directory).resolve()
    archived={archive_path(entry):entry for entry in expected.values()}
    stages={p.split('/Contents/',1)[0]+'/' for p,e in archived.items()
            if p!=e['path'] and '/Contents/'in p}
    for file in directory.rglob('*'):
        if not file.is_file():continue
        relative=file.relative_to(directory).as_posix()
        if relative.startswith('{')and (not relative.startswith('{tmp}/')or any(relative.startswith(s)for s in stages))and relative not in archived:
            raise ValueError('Delivered installer has an undeclared installed file: '+relative)
    for relative,entry in expected.items():
        file=directory/archive_path(entry)
        if not file.resolve().is_relative_to(directory)or not file.is_file()or file.stat().st_size!=entry['bytes']:
            raise ValueError('Delivered installer payload is missing or has a different size: '+relative)
        with file.open('rb')as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
        if actual!=entry['sha256']:raise ValueError('Delivered installer payload hash differs: '+relative)
