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
import datetime
import signal
import time
from compilation_identity import fingerprint

SSH = ["ssh", "-i", str(Path.home() / ".ssh/fatberry-build"), "-o", "BatchMode=yes",
       "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=20", os.environ["RUNNER_SSH_HOST"]]


def output_prefix(platform: str, slug: str) -> str:
    run_id = os.environ["GITHUB_RUN_ID"]
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    if not run_id.isdigit() or not attempt.isdigit():
        raise ValueError("Invalid GitHub run identity")
    return f"output/runs/{run_id}/{attempt}/{platform}/{slug}"


def transfer(operation: str, remote: str, local: Path) -> None:
    local.parent.mkdir(parents=True, exist_ok=True)
    if (operation=='put' and (local.name in {'installer-mac.dmg','installer-win.exe'}
            or local.name=='compiled.tar.gz'and local.stat().st_size>=64*1024**2)
            and os.environ.get('FATBERRY_PRIVATE_ASSET_TOKEN')):
        from private_objects import Store
        location=Store().upload(local)
        if location['sha256']!=digest(local):raise ValueError('Private release object changed during upload')
        locator=local.with_name(local.name+'.storage.json');locator.write_text(json.dumps(location,indent=2)+'\n')
        transfer('put',remote+'.storage.json',locator)
        return
    for attempt in range(3):
        try:
            with local.open("wb" if operation == "get" else "rb") as stream:
                subprocess.run(SSH + [f"{operation} {remote}"], check=True, timeout=1800,
                               **({"stdout": stream} if operation == "get" else {"stdin": stream}))
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def input_object(entry,root):
    target=root/entry['file']
    if entry.get('storage'):
        from private_objects import Store
        Store().download(entry['storage'],target)
    else:transfer('get','input/'+entry['file'],target)
    if digest(target)!=entry['sha256']:raise ValueError('Private input object checksum differs')
    return target


def main() -> None:
    slug = sys.argv[1]
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise SystemExit("Invalid product slug")
    root = Path("work").resolve()
    root.mkdir()
    log = root / "build.log"
    status = {"slug": slug, "status": "running", "stage": "input",
              "platform": "macos",
              "runUrl": f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
              "runAttempt": os.environ.get("GITHUB_RUN_ATTEMPT", "1")}
    def checkpoint(stage: str, state: str = "running") -> None:
        status.update(stage=stage, status=state, updatedAt=datetime.datetime.now(datetime.timezone.utc).isoformat())
        status_file = root / "status.json"
        status_file.write_text(json.dumps(status) + "\n")
        transfer("put", f"{output_prefix('macos', slug)}/status.json", status_file)
    try:
        index_hash = os.environ["INPUT_INDEX_SHA256"]
        if not re.fullmatch(r"[0-9a-f]{64}", index_hash):
            raise ValueError("A pinned private input index is required")
        transfer("get", f"input/index-{index_hash}.json", root / "index.json")
        if digest(root / "index.json") != index_hash:
            raise ValueError("Private input index hash mismatch")
        index = json.loads((root / "index.json").read_text())
        product = next(p for p in index["products"] if p["slug"] == slug)
        if "compilationFingerprint" in product:
            if fingerprint(index, product) != product["compilationFingerprint"]:
                raise ValueError("The explicit compilation contract differs from its fingerprint")
            status["compilationFingerprint"] = product["compilationFingerprint"]
        status.update(sourceSnapshot=product["sourceSnapshot"], inputIndexSha256=index_hash)
        checkpoint("input")
        with log.open("w") as output:
            def run(*command: str, cwd: Path | None = None) -> None:
                limit = 2700 if status["stage"] == "compile" else 2400 if status["stage"] == "validate-and-package" else 1200
                process = subprocess.Popen(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    result = process.wait(timeout=limit)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise
                if result:
                    raise subprocess.CalledProcessError(result, command)

            for item in [*index["dependencies"], *product.get("dependencies", []), *product["archives"]]:
                archive = input_object(item,root)
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
            checkpoint("tools")
            from private_file_inputs import restore as restore_private_files
            restore_private_files(root / 'product', product['sourceSnapshot'], product.get('privateFiles', []), transfer)
            run(sys.executable, "-m", "venv", str(root / "tools"))
            run(str(root / "tools/bin/pip"), "install", "cmake==4.2.3", "ninja")
            os.environ["PATH"] = str(root / "tools/bin") + os.pathsep + os.environ["PATH"]
            run("arch", "-x86_64", "/usr/bin/true")
            source = root / "product"
            if product.get("pythonRequirements"):
                requirement = product["pythonRequirements"]
                if requirement.get("pythonVersion") != ".".join(map(str, sys.version_info[:3])):
                    raise ValueError("The runner Python differs from the product's pinned build interpreter")
                requirement_file = (source / requirement["path"]).resolve()
                if not requirement_file.is_relative_to(source) or digest(requirement_file) != requirement["sha256"]:
                    raise ValueError("The product's pinned Python build requirements changed")
                run(str(root / "tools/bin/pip"), "install", "-r", str(requirement_file))
            build = root / "build"
            client = root / "fat-berry-licensing-client"
            tooling = root / "fat-berry-release-tooling"
            # Proprietary source is still captured from the local working tree.
            # Only SDKs proved clean locally can use native, pinned Git checkout.
            from public_sdk_inputs import restore
            restore(source, product["sourceSnapshot"], run)
            run(sys.executable, str(tooling / "tools/verify_source_snapshot.py"), str(source), product["sourceSnapshot"])
            if product.get('resourceLocatorChecks'):
                checkpoint('test-resource-locators')
                from resource_locator_checks import validate as validate_resource_locators
                validate_resource_locators(source,root,product['resourceLocatorChecks'],run)
            # Check frozen runtime inputs before spending time compiling. Local
            # repository paths do not exist in the runner's flattened capture.
            for entry in product.get('supportFiles', []):
                if entry.get('generated') is True:
                    continue
                support_file = Path(entry['source'].replace('{root}', str(root))).resolve()
                if (not support_file.is_relative_to(root) or not support_file.is_file()
                        or support_file.stat().st_size != entry['bytes'] or digest(support_file) != entry['sha256']):
                    raise ValueError('Frozen product support input is absent or changed: ' + entry['name'])
            framework_arguments = []
            toolchain = subprocess.check_output(["xcodebuild", "-version"], text=True).strip()
            cached = product.get("compiledCache")
            restored = False
            if cached and cached["toolchain"] == toolchain:
                checkpoint("restore-compiled-bundles")
                archive = input_object(cached,root)
                if digest(archive) != cached["sha256"] or cached["compilationFingerprint"] != product["compilationFingerprint"]:
                    raise ValueError("Compiled cache differs from the exact compilation contract")
                with tarfile.open(archive) as source_archive:
                    source_archive.extractall(build / "CachedBundles", filter="data")
                archive.unlink()
                restored = True
            if not restored and product.get("framework") == "native":
                run("cmake", "-S", str(root / "nlohmann-json"), "-B", str(root / "nlohmann-build"),
                    "-DJSON_BuildTests=OFF", "-DJSON_Install=ON", f"-DCMAKE_INSTALL_PREFIX={root / 'nlohmann-install'}")
                run("cmake", "--install", str(root / "nlohmann-build"))
            if not restored and product.get("framework") == "iplug2":
                checkpoint("prepare-framework")
                for dependency in ("iPlug2", "vst3sdk", "clap", "clap-helpers"):
                    run("git", "-C", str(source / "vendor" / dependency), "status", "--porcelain", "--untracked-files=all")
                run(sys.executable, str(source / "scripts/prepare-framework.py"), cwd=source)
                layout = json.loads((source / "third-party/framework-layout.json").read_text())
                framework = (source / layout["iplug2_dir"]).resolve()
                if not framework.is_relative_to(source / "vendor/derived"):
                    raise ValueError("The prepared iPlug2 framework escapes its product checkout")
                framework_arguments.append(f"-DIPLUG2_DIR={framework}")
            checkpoint("configure" if not restored else "restore-complete")
            configure = ["cmake", "-S", str(source), "-B", str(build), "-G", "Xcode",
                         "-DCMAKE_OSX_ARCHITECTURES=arm64;x86_64", "-DCMAKE_BUILD_TYPE=Release",
                         f"-DCMAKE_OSX_DEPLOYMENT_TARGET={product['minimumOS']}",
                         "-DCMAKE_XCODE_ATTRIBUTE_CODE_SIGN_IDENTITY=-", "-DCMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_REQUIRED=NO",
                         "-DFATBERRY_PRODUCTION_BUILD=ON", "-DFATBERRY_LICENSING_BUILD_TESTS=OFF",
                         f"-DFATBERRY_KEYGEN_PRODUCT_ID={product['keygenProductId']}",
                         f"-DFATBERRY_KEYGEN_PUBLIC_KEY={index['keygenPublicKey']}",
                         f"-DFATBERRY_LICENSING_API_ORIGIN={index['licensingApiOrigin']}",
                         f"-DFATBERRY_SODIUM_MACOS_SHA256={index['sodiumSha256']}",
                         f"-DFATBERRY_LICENSING_SOURCE_DIR={client}",
                         "-DFATBERRY_LICENSING_SNAPSHOT_SHA256=" + next(d["sourceSnapshot"] for d in index["dependencies"] if d["directory"] == "fat-berry-licensing-client"),
                         f"-DFATBERRY_RELEASE_TOOLING_DIR={tooling}",
                         "-DSMTG_RUN_VST_VALIDATOR=OFF", "-DSMTG_CREATE_PLUGIN_LINK=OFF",
                         *([f"-DPython3_EXECUTABLE={root / 'tools/bin/python'}"] if product.get("pythonRequirements") else []),
                         *[arg.replace("{root}", str(root)) for arg in product["cmakeArguments"]], *framework_arguments]
            if not restored:
                run(*configure)
                checkpoint("compile")
                run("cmake", "--build", str(build), "--config", "Release", "--parallel", "3",
                    "--target", product["vst3Target"], product["auTarget"])
            def bundle(name: str) -> Path:
                found = [p for p in build.rglob(name) if p.is_dir()]
                if len(found) != 1:
                    raise ValueError("Expected exactly one fresh format bundle")
                return found[0]
            if not restored:
                # Xcode also emits one thin intermediate per architecture.
                # Select the real universal product and reject conflicting copies.
                helpers = []
                for candidate in build.rglob("fatberry-keychain-store"):
                    if not candidate.is_file():
                        continue
                    check = subprocess.run(["lipo", str(candidate), "-verify_arch", "arm64", "x86_64"],
                                           stdout=output, stderr=output, timeout=20)
                    if check.returncode == 0:
                        helpers.append(candidate)
                if not helpers or len({digest(p) for p in helpers}) != 1:
                    raise ValueError("The fresh universal macOS licensing helper is missing or ambiguous")
                for name in (product["vst3Bundle"], product["auBundle"]):
                    outer = bundle(name)
                    for target in [outer, *[p for p in outer.rglob("*.vst3") if p.is_dir()]]:
                        destination = target / "Contents/Resources/fatberry-keychain-store"
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(helpers[0], destination)
                        destination.chmod(0o755)
            checkpoint("save-compiled-bundles")
            compiled = root / "compiled.tar.gz"
            with tarfile.open(compiled, "w:gz", compresslevel=1) as archive:
                for name in (product["vst3Bundle"], product["auBundle"]):
                    archive.add(bundle(name), arcname=name)
            transfer("put", f"{output_prefix('macos', slug)}/compiled.tar.gz", compiled)
            receipt = root / "compiled.json"
            receipt.write_text(json.dumps({"sourceSnapshot": product["sourceSnapshot"],
                "compilationFingerprint": product.get("compilationFingerprint"), "toolchain": toolchain,
                "inputIndexSha256": index_hash, "sha256": digest(compiled), "bytes": compiled.stat().st_size,
                "dependencies": [*index["dependencies"], *product.get("dependencies", [])], "product": product}) + "\n")
            transfer("put", f"{output_prefix('macos', slug)}/compiled.json", receipt)
            compiled.unlink()
            checkpoint("validate-and-package")
            validator = next(p for p in (root / "pluginval").rglob("pluginval") if p.is_file())
            run("chmod", "+x", str(validator))
            run("xattr", "-cr", str(root / "pluginval"))
            product_file = root / "product.json"
            if index.get('macKeychainHelper'):
                helper = index['macKeychainHelper']
                target = root / helper['file']
                transfer('get', 'input/' + helper['file'], target)
                if digest(target) != helper['sha256'] or target.stat().st_size != helper['bytes']:
                    raise ValueError('Shared Keychain helper artifact changed')
                if digest(client / 'src/MacKeychainStore.mm') != helper['sourceSha256']:
                    raise ValueError('Shared Keychain helper source differs from compiled client')
                product['macKeychainHelper'] = helper
            product.update(inputIndexSha256=index_hash, dependencySnapshots={d["directory"]: d.get("sourceSnapshot", d["sha256"]) for d in [*index["dependencies"], *product.get("dependencies", [])]})
            product_file.write_text(json.dumps(product))
            run(sys.executable, str(tooling / "tools/package_staged_macos.py"),
                "--product", str(product_file), "--vst3", str(bundle(product["vst3Bundle"])),
                "--au", str(bundle(product["auBundle"])), "--pluginval", str(validator), "--output", str(root / "output"))
            checkpoint("upload")
            for path in (root / "output").iterdir():
                if path.is_file():
                    transfer("put", f"{output_prefix('macos', slug)}/{path.name}", path)
            checkpoint("complete", "passed")
    except Exception as error:
        # The public log never receives compiler output or private source snippets.
        with log.open("a") as stream:
            stream.write(f"\n{type(error).__name__}: {error}\n")
        status["status"] = "failed"
    finally:
        if log.exists():
            transfer("put", f"{output_prefix('macos', slug)}/build.log", log)
        checkpoint(status["stage"], status["status"])
    print(f"{slug}: {status['status']} ({status['stage']}); detailed diagnostics stored privately")
    if status["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
