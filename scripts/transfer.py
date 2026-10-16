#!/usr/bin/env python3
"""Restricted SSH object transfer, installed privately with a forced command.

The SSH key can read this batch's inputs and write this batch's outputs. It
cannot run shell commands, read other files, or modify customer artifacts.
"""
from __future__ import annotations
import os
from pathlib import Path
import shlex
import sys
import json
import time
import shutil
import re


def main() -> None:
    root = Path(sys.argv[1]).resolve(strict=True)
    command = shlex.split(os.environ.get("SSH_ORIGINAL_COMMAND", ""))
    if len(command) != 2 or command[0] not in {"get", "put"}:
        raise SystemExit("Only get/put object transfers are permitted")
    operation, relative = command
    parts = Path(relative).parts
    expected = "input" if operation == "get" else "output"
    if not parts or parts[0] != expected or ".." in parts or Path(relative).is_absolute():
        raise SystemExit("Invalid object path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root / expected):
        raise SystemExit("Object escapes transfer directory")
    if operation == "get":
        with path.open("rb") as source:
            if path.parent == root / "input" and re.fullmatch(r"(?:activation|build)-continuation-[0-9a-f]{64}\.json", path.name):
                claims = root / ".private/continuation-claims"
                claims.mkdir(mode=0o700, parents=True, exist_ok=True)
                try:
                    os.link(path, claims / path.name)
                except FileExistsError:
                    raise SystemExit("Continuation already claimed; duplicate dispatch suppressed")
            while block := source.read(1024 * 1024):
                sys.stdout.buffer.write(block)
    else:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = path.with_name(path.name + f".part-{os.getpid()}")
        size = 0
        try:
            with temporary.open("xb") as destination:
                os.chmod(temporary, 0o600)
                while block := sys.stdin.buffer.read(1024 * 1024):
                    size += len(block)
                    if size > 60 * 1024**3:
                        raise ValueError("Object exceeds transfer limit")
                    if size % (32 * 1024**2) < len(block) and shutil.disk_usage(root).free < 512 * 1024**2:
                        raise ValueError("Insufficient disk reserve; production storage protected")
                    destination.write(block)
            os.replace(temporary, path)
            if path.name == "status.json":
                # systemd.path wakes the reconciler only when a runner reports.
                # It has no GitHub token and never invokes a language model.
                event = json.dumps({"path": relative, "receivedAt": int(time.time())}) + "\n"
                with (root / "events.log").open("a") as events:
                    events.write(event)
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
