#!/usr/bin/env python3
"""Fetch private, hashed inputs and upload private diagnostics and Mac outputs."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

SSH = ["ssh", "-i", str(Path.home() / ".ssh/fatberry-build"), "-o", "BatchMode=yes",
       "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=20", os.environ["RUNNER_SSH_HOST"]]


def transfer(operation: str, remote: str, local: Path) -> None:
    local.parent.mkdir(parents=True, exist_ok=True)
    with local.open("wb" if operation == "get" else "rb") as stream:
        subprocess.run(SSH + [f"{operation} {remote}"], check=True,
                       **({"stdout": stream} if operation == "get" else {"stdin": stream}))


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    slug = sys.argv[1]
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise SystemExit("Invalid product slug")
    root = Path("work").resolve()
    root.mkdir()
    log = root / "build.log"
    status = {"slug": slug, "status": "failed", "stage": "input"}
    try:
        transfer("get", "input/index.json", root / "index.json")
        index = json.loads((root / "index.json").read_text())
        product = next(p for p in index["products"] if p["slug"] == slug)
        with log.open("w") as output:
            def run(*command: str, cwd: Path | None = None) -> None:
                subprocess.run(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT, check=True)

            for item in [*index["dependencies"], *product["archives"]]:
                archive = root / item["file"]
                transfer("get", f"input/{item['file']}", archive)
                if digest(archive) != item["sha256"]:
                    raise ValueError("Private input archive hash mismatch")
                destination = root / item["directory"]
                destination.mkdir(parents=True, exist_ok=True)
                if archive.suffix == ".zip":
                    with zipfile.ZipFile(archive) as source:
                        source.extractall(destination)
                else:
                    with tarfile.open(archive) as source:
                        source.extractall(destination, filter="data")
                archive.unlink()
            status["stage"] = "tools"
            run(sys.executable, "-m", "venv", str(root / "tools"))
            run(str(root / "tools/bin/pip"), "install", "cmake==4.2.3", "ninja")
            os.environ["PATH"] = str(root / "tools/bin") + os.pathsep + os.environ["PATH"]
            run("arch", "-x86_64", "/usr/bin/true")
            source = root / "product"
            build = root / "build"
            client = root / "fat-berry-licensing-client"
            tooling = root / "fat-berry-release-tooling"
            status["stage"] = "configure"
            configure = ["cmake", "-S", str(source), "-B", str(build), "-G", "Xcode",
                         "-DCMAKE_OSX_ARCHITECTURES=arm64;x86_64", "-DCMAKE_BUILD_TYPE=Release",
                         f"-DCMAKE_OSX_DEPLOYMENT_TARGET={product['minimumOS']}",
                         "-DCMAKE_XCODE_ATTRIBUTE_CODE_SIGN_IDENTITY=-", "-DCMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_REQUIRED=NO",
                         "-DFATBERRY_PRODUCTION_BUILD=ON", "-DFATBERRY_LICENSING_BUILD_TESTS=OFF",
                         f"-DFATBERRY_SODIUM_MACOS_SHA256={index['sodiumSha256']}",
                         f"-DFATBERRY_LICENSING_SOURCE_DIR={client}",
                         f"-DFATBERRY_RELEASE_TOOLING_DIR={tooling}",
                         "-DSMTG_RUN_VST_VALIDATOR=OFF", "-DSMTG_CREATE_PLUGIN_LINK=OFF",
                         *[arg.replace("{root}", str(root)) for arg in product["cmakeArguments"]]]
            run(*configure)
            status["stage"] = "compile"
            run("cmake", "--build", str(build), "--config", "Release", "--parallel", "3",
                "--target", product["vst3Target"], product["auTarget"])
            def bundle(name: str) -> Path:
                found = [p for p in build.rglob(name) if p.is_dir()]
                if len(found) != 1:
                    raise ValueError("Expected exactly one fresh format bundle")
                return found[0]
            status["stage"] = "validate-and-package"
            validator = next(p for p in (root / "pluginval").rglob("pluginval") if p.is_file())
            run("chmod", "+x", str(validator))
            run("xattr", "-cr", str(root / "pluginval"))
            product_file = root / "product.json"
            product_file.write_text(json.dumps(product))
            run(sys.executable, str(tooling / "tools/package_staged_macos.py"),
                "--product", str(product_file), "--vst3", str(bundle(product["vst3Bundle"])),
                "--au", str(bundle(product["auBundle"])), "--pluginval", str(validator), "--output", str(root / "output"))
            status["stage"] = "upload"
            for path in (root / "output").iterdir():
                if path.is_file():
                    transfer("put", f"output/{slug}/{path.name}", path)
            status.update(status="passed", stage="complete")
    except Exception as error:
        # The public log never receives compiler output or private source snippets.
        with log.open("a") as stream:
            stream.write(f"\n{type(error).__name__}: {error}\n")
    finally:
        status_file = root / "status.json"
        status_file.write_text(json.dumps(status) + "\n")
        transfer("put", f"output/{slug}/status.json", status_file)
        if log.exists():
            transfer("put", f"output/{slug}/build.log", log)
    print(f"{slug}: {status['status']} ({status['stage']}); detailed diagnostics stored privately")
    if status["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
