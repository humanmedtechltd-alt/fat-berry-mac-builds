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
            actual = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], timeout=30).decode().strip()
            dirty = subprocess.check_output(["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=all"], timeout=60)
            if actual != revision or dirty.strip():
                raise ValueError("The restored public SDK differs from the clean local pin")
