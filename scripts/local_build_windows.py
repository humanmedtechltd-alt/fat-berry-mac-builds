#!/usr/bin/env python3
"""Run build_windows.py on a Windows PC from local inputs (no Actions, no SSH queue).

Usage: local_build_windows.py SLUG INDEX_SHA256 INPUTS OUTPUTS [--package-only]

INPUTS holds input/windows-index-<sha>.json, input/windows/<slug>/installer-win.exe
and every other input/<file> the index names. OUTPUTS receives what the queue
would have received, at the same paths.

build_windows.py's installation and wizard checks were written for a disposable
hosted Windows runner: they uninstall the PC's WebView2 Runtime and Visual C++
runtime first. With --package-only they are not run here: the installer is
built and its final payload verified, and test_windows_upgrade.py tests it on
a developer PC without removing anything.
"""
import json
import os
import shutil
import sys
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def contained(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("Unsafe path: " + relative)
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("Path escapes its root: " + relative)
    return target


def main() -> None:
    slug, index_sha, inputs, outputs = sys.argv[1:5]
    package_only = "--package-only" in sys.argv[5:]
    inputs, outputs = Path(inputs).resolve(), Path(outputs).resolve()
    index = json.loads((inputs / "input" / f"windows-index-{index_sha}.json").read_bytes())
    product = next(p for p in index["products"] if p["slug"] == slug)
    for entry in [*index["dependencies"], *product.get("additions", []), *product.get("replacements", [])]:
        if entry.get("storage"):
            raise ValueError("Local builds take every input from INPUTS: " + entry["file"])

    os.environ.pop("FATBERRY_PRIVATE_ASSET_TOKEN", None)
    os.environ["RUNNER_SSH_HOST"] = "local-inputs"
    os.environ["INPUT_INDEX_SHA256"] = index_sha
    os.environ.setdefault("GITHUB_REPOSITORY", "humanmedtechltd-alt/fat-berry-mac-builds")
    os.environ.setdefault("GITHUB_RUN_ID", time.strftime("%Y%m%d%H%M%S"))
    os.environ.setdefault("GITHUB_RUN_ATTEMPT", "1")

    import build_product

    def transfer(operation: str, remote: str, local: Path) -> None:
        local = Path(local)
        local.parent.mkdir(parents=True, exist_ok=True)
        if operation == "get":
            shutil.copyfile(contained(inputs, remote), local)
        elif operation == "put":
            destination = contained(outputs, remote)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local, destination)
        else:
            raise ValueError("Unknown transfer operation")

    build_product.transfer = transfer
    if package_only:
        install = types.ModuleType("test_windows_install")
        install.validate_install = lambda *args, **kwargs: {"installation": "pending-developer-pc-upgrade-test"}
        wizard = types.ModuleType("test_windows_wizard")
        wizard.validate_wizard = lambda *args, **kwargs: {"wizard": "not-run-developer-pc"}
        sys.modules["test_windows_install"] = install
        sys.modules["test_windows_wizard"] = wizard
    import build_windows  # Imports build_product's (patched) transfer.
    sys.argv = [str(HERE / "build_windows.py"), slug]
    build_windows.main()


if __name__ == "__main__":
    main()
