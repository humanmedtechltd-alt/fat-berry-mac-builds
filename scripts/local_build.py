#!/usr/bin/env python3
"""Run build_product.py on a disposable Mac from local inputs (no Actions, no SSH queue).

Usage: local_build.py SLUG INDEX_SHA256 INPUTS OUTPUTS

INPUTS holds input/index-<sha>.json and every input/<file> that index names,
copied in beforehand; no entry may point at remote storage. OUTPUTS receives
the files build_product.py would have sent to the queue, at the same paths.

A product may carry "sourceChange": a folder under INPUTS with change.json,
files/<path> and the new FATBERRY-SOURCE-SNAPSHOT.json. It is applied to the
extracted capture before the snapshot is checked, so the build still verifies
every source byte against the pinned (new) snapshot hash.

package_staged_macos.py installs the delivered PKG with sudo and runs only on a
disposable hosted Mac. A Codemagic build machine is one: it is deleted when
the build ends. This script therefore sets the variables that check reads.
"""
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def contained(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(p in {"..", ".git"} for p in path.parts):
        raise ValueError("Unsafe path: " + relative)
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("Path escapes its root: " + relative)
    return target


def apply_change(source: Path, folder: Path, expected: str) -> None:
    change = json.loads((folder / "change.json").read_text())
    manifest = source / "FATBERRY-SOURCE-SNAPSHOT.json"
    if sha256(manifest.read_bytes()) != change["baseSnapshot"]:
        raise ValueError("The extracted capture is not the change's base snapshot")
    new_manifest = (folder / "FATBERRY-SOURCE-SNAPSHOT.json").read_bytes()
    if sha256(new_manifest) != change["newSnapshot"] or change["newSnapshot"] != expected:
        raise ValueError("The new manifest differs from the pinned snapshot")
    for record in change["files"]:
        if "status" not in record:
            continue  # A note about how the file was merged.
        data = (folder / "files" / record["path"]).read_bytes()
        if sha256(data) != record["sha256"] or len(data) != record["bytes"]:
            raise ValueError("Changed file differs from its record: " + record["path"])
        target = contained(source, record["path"])
        if record["status"] == "A" and target.exists():
            raise ValueError("Added file already exists: " + record["path"])
        if record["status"] == "M" and not target.is_file():
            raise ValueError("Modified file is missing: " + record["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    manifest.write_bytes(new_manifest)
    print(f"Applied source change {change['baseSnapshot'][:12]} -> {change['newSnapshot'][:12]} "
          f"({change['sourceCommit'][:12]})", flush=True)


def main() -> None:
    slug, index_sha, inputs, outputs = sys.argv[1:5]
    inputs, outputs = Path(inputs).resolve(), Path(outputs).resolve()
    index = json.loads((inputs / "input" / f"index-{index_sha}.json").read_bytes())
    product = next(p for p in index["products"] if p["slug"] == slug)
    for entry in [*index["dependencies"], *product.get("dependencies", []), *product["archives"],
                  *product.get("supportInputs", [])]:
        if entry.get("storage"):
            raise ValueError("Local builds take every input from INPUTS: " + entry["file"])

    os.environ.pop("FATBERRY_PRIVATE_ASSET_TOKEN", None)
    os.environ["RUNNER_SSH_HOST"] = "local-inputs"
    os.environ["INPUT_INDEX_SHA256"] = index_sha
    os.environ.setdefault("GITHUB_REPOSITORY", "humanmedtechltd-alt/fat-berry-mac-builds")
    os.environ.setdefault("GITHUB_RUN_ID", time.strftime("%Y%m%d%H%M%S"))
    os.environ.setdefault("GITHUB_RUN_ATTEMPT", "1")
    os.environ.update(GITHUB_ACTIONS="true", FATBERRY_RUNNER_ENVIRONMENT="github-hosted", RUNNER_OS="macOS")

    import build_product
    import public_sdk_inputs

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
    original_restore = public_sdk_inputs.restore
    change = product.get("sourceChange")

    def restore(source, expected, run):
        if change:
            apply_change(Path(source), contained(inputs, change), expected)
        return original_restore(source, expected, run)

    public_sdk_inputs.restore = restore
    sys.argv = [str(HERE / "build_product.py"), slug]
    build_product.main()


if __name__ == "__main__":
    main()
