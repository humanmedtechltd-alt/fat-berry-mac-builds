"""Restore explicitly pinned, clean public SDKs from a private source manifest."""
import hashlib
import json
from pathlib import Path
import re
import subprocess


def contained(root, relative):
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(p in {"..", ".git", ".private"} for p in path.parts):
        raise ValueError("Invalid public SDK destination")
    destination = (root / path).resolve()
    if not destination.is_relative_to(root.resolve()) or destination == root.resolve():
        raise ValueError("Public SDK escapes its source root")
    return destination


def restore_captured_framework_objects(source, run):
    """Repair portable Git metadata without recapturing private product bytes."""
    lock=json.loads((source/'third-party/source-lock.json').read_text(encoding='utf-8-sig'))
    required={'iPlug2','vst3sdk','clap','clap-helpers'}
    selected=[item for item in lock['sources']if item['name']in required]
    if {item['name']for item in selected}!=required:raise ValueError('Missing pinned framework source')
    for item in selected:
        url=item['repository_url'];revision=item['revision'];destination=contained(source,item['destination'])
        if(not re.fullmatch(r'https://github\.com/(?!humanmedtechltd-alt/)[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?',url)
                or not re.fullmatch('[0-9a-f]{40}',revision)):
            raise ValueError('Framework object restoration requires an exact public SDK pin')
        actual=subprocess.check_output(['git','-C',str(destination),'rev-parse','HEAD'],text=True,timeout=30).strip()
        if actual!=revision:raise ValueError('Captured SDK history differs from its source lock')
        # --refetch supplies the pinned tree's missing original blobs even
        # when the shallow commit is already present. No checkout/reset occurs:
        # the captured working files and reviewed patches remain untouched.
        run('git','-c','protocol.file.allow=never','-C',str(destination),'fetch','--quiet',
            '--refetch','--no-filter','--depth=1','--no-write-fetch-head',url,revision)


def restore(source, expected, run):
    raw = (source / "FATBERRY-SOURCE-SNAPSHOT.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("The source manifest differs from the frozen product snapshot")
    manifest = json.loads(raw)
    for item in manifest.get("externalInputs", []):
        if (item.get("captureMethod") != "clean-public-git-commit"
                or not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", item["url"])
                or not re.fullmatch(r"[0-9a-f]{40}", item["commit"])):
            raise ValueError("Invalid explicit public SDK input")
        destination = contained(source, item["path"])
        if destination.exists() and any(destination.iterdir()):
            raise ValueError("A pinned SDK destination already contains unexpected files")
        destination.mkdir(parents=True, exist_ok=True)
        run("git", "init", "--quiet", str(destination))
        run("git", "-C", str(destination), "remote", "add", "origin", item["url"])
        run("git", "-C", str(destination), "fetch", "--quiet", "--depth", "1", "origin", item["commit"])
        run("git", "-C", str(destination), "checkout", "--quiet", "--detach", "FETCH_HEAD")
        run("git", "-c", "protocol.file.allow=never", "-C", str(destination), "submodule", "update", "--init", "--recursive", "--depth", "1")
        for checkout, revision in [(destination, item["commit"]),
                                    *[(contained(destination, p["path"]), p["commit"]) for p in item["submodules"]]]:
            if not re.fullmatch(r"[0-9a-f]{40}", revision):
                raise ValueError("Invalid public SDK submodule revision")
            actual = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], stderr=subprocess.STDOUT, timeout=30).decode().strip()
            dirty = subprocess.check_output(["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=all"], stderr=subprocess.STDOUT, timeout=60)
            if actual != revision or dirty.strip():
                raise ValueError("The restored public SDK differs from the clean local pin")
    # Independent shallow Git identities intentionally omit local URL/credential
    # configuration. Restore registration from the pinned .gitmodules files,
    # without fetching, checking out or altering any captured source bytes.
    for folder in [source, *[contained(source, p["path"]) for p in manifest.get("submodules", [])]]:
        if (folder / ".gitmodules").is_file():
            run("git", "-C", str(folder), "submodule", "init")
