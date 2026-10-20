"""Restore explicitly hashed runtime files without copying them into source archives."""
from pathlib import Path
import os,re

def restore(root,entries,input_object,digest):
    root=Path(root).resolve()
    for entry in entries:
        relative=Path(entry['path'])
        if relative.is_absolute()or '..'in relative.parts or relative.parts[0]!='runtime-content':
            raise ValueError('Runtime content must stay in its dedicated workspace directory')
        if not re.fullmatch('[0-9a-f]{64}',entry['sha256'])or entry['bytes']<=0:
            raise ValueError('Runtime content identity is invalid')
        destination=root/relative
        if destination.exists()or not destination.resolve().is_relative_to(root):raise ValueError('Runtime content destination already exists or escapes its workspace')
        source=input_object(entry,root)
        if source.stat().st_size!=entry['bytes']or digest(source)!=entry['sha256']:raise ValueError('Runtime content differs from captured bytes')
        destination.parent.mkdir(parents=True,exist_ok=True);os.replace(source,destination)
