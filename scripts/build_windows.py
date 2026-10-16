#!/usr/bin/env python3
"""Repackage audited Windows payloads, preserving per-product destinations/IDs."""
from __future__ import annotations
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile

from build_product import transfer, digest, output_prefix


def main() -> None:
    slug = sys.argv[1]
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("Invalid product slug")
    root = Path("work").resolve()
    root.mkdir()
    log = root / "build.log"
    status = {"slug": slug, "platform": "windows", "status": "running", "stage": "input",
              "runUrl": f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"}
    def checkpoint(stage: str, state: str = "running") -> None:
        status.update(stage=stage, status=state, updatedAt=datetime.datetime.now(datetime.timezone.utc).isoformat())
        path = root / "status.json"
        path.write_text(json.dumps(status) + "\n")
        transfer("put", f"{output_prefix('windows', slug)}/status.json", path)
    try:
        index_hash = os.environ["INPUT_INDEX_SHA256"]
        if not re.fullmatch(r"[a-f0-9]{64}", index_hash):
            raise ValueError("A pinned Windows input index is required")
        transfer("get", f"input/windows-index-{index_hash}.json", root / "index.json")
        if digest(root / "index.json") != index_hash:
            raise ValueError("Private Windows index hash mismatch")
        index = json.loads((root / "index.json").read_text())
        product = next(p for p in index["products"] if p["slug"] == slug)
        status.update(buildInputSha256=product["buildInputSha256"], inputIndexSha256=index_hash)
        checkpoint("input")
        with log.open("w", encoding="utf-8") as output:
            def run(*args: str, capture: bool = False) -> str:
                if capture:
                    result = subprocess.run(args, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
                    output.write(result.stdout + result.stderr)
                    output.flush()
                    return result.stdout
                process = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT)
                try:
                    result = process.wait(timeout=1800)
                except subprocess.TimeoutExpired:
                    subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], stdout=output, stderr=output, timeout=30, check=False)
                    process.wait(timeout=30)
                    raise
                if result:
                    raise subprocess.CalledProcessError(result, args)
                return ""
            for item in index["dependencies"]:
                archive = root / item["file"]
                transfer("get", f"input/{item['file']}", archive)
                if digest(archive) != item["sha256"]:
                    raise ValueError("Private Windows dependency hash mismatch")
                destination = root / item["directory"]
                destination.mkdir()
                if archive.suffix == ".zip":
                    with zipfile.ZipFile(archive) as source:
                        source.extractall(destination)
                else:
                    with tarfile.open(archive) as source:
                        source.extractall(destination, filter="data")
                archive.unlink()
            innounp = root / "tools/innounp.exe"
            original = root / "original.exe"
            transfer("get", f"input/windows/{slug}/installer-win.exe", original)
            if digest(original) != product["original"]["sha256"]:
                raise ValueError("Original installer hash mismatch")
            checkpoint("extract-and-inventory")
            payload = root / "payload"
            run(str(innounp), "-t", "-b", "-q", str(original))
            run(str(innounp), "-x", "-a", "-m", "-y", "-b", f"-d{payload}", str(original))
            for entry in product["payloadFiles"]:
                file = payload / entry["path"]
                if not file.is_file() or file.stat().st_size != entry["bytes"] or digest(file) != entry["sha256"]:
                    raise ValueError("Original product-specific payload differs from audited inventory")
            for replacement in product.get("replacements", []):
                destination = payload / replacement["path"]
                transfer("get", "input/" + replacement["file"], destination)
                if destination.stat().st_size != replacement["bytes"] or digest(destination) != replacement["sha256"]:
                    raise ValueError("Replacement product content hash mismatch")
            for addition in product.get("additions", []):
                destination = payload / addition["path"]
                if destination.exists() or not destination.resolve().is_relative_to(payload.resolve()):
                    raise ValueError("Additional content conflicts with an existing file or escapes the payload")
                transfer("get", "input/" + addition["file"], destination)
                if destination.stat().st_size != addition["bytes"] or digest(destination) != addition["sha256"]:
                    raise ValueError("Additional product content hash mismatch")
            module = payload / product["modulePath"]
            if digest(module) != product["original"]["vst3Sha256"]:
                raise ValueError("Audited plugin module changed")
            # The dependency is supplied as a pinned Microsoft-signed input.
            redist = root / "tools/VC_redist.x64.exe"
            if digest(redist) != index["runtime"]["sha256"]:
                raise ValueError("Microsoft runtime hash mismatch")
            # Hosted runners launch in PowerShell 7; use the same runtime so an
            # inherited PSModulePath cannot load PS7 modules into PowerShell 5.
            powershell = "pwsh.exe"
            run(powershell, "-NoProfile", "-Command",
                "$s=Get-AuthenticodeSignature -LiteralPath '" + str(redist).replace("'", "''") +
                "'; if($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'Microsoft Corporation'){exit 1}")
            final = root / "release/installer-win.exe"
            final.parent.mkdir()
            if product["needsRepackage"]:
                checkpoint("render-and-package")
                tooling = root / "fat-berry-release-tooling"
                kit = tooling / "packaging/windows/installer-design"
                design = root / "design"
                installed_bytes = sum(e["bytes"] for e in product["payloadFiles"])
                installed_bytes += sum(e["bytes"] - e["oldBytes"] for e in product.get("replacements", []))
                installed_bytes += sum(e["bytes"] for e in product.get("additions", []))
                run(powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(kit / "Render-InstallerDesign.ps1"),
                    "-ProductName", product["name"], "-ProductVersion", product["version"],
                    "-BundleName", product["vst3Bundle"], "-DiskSpaceMb", f"{installed_bytes / 1024**2:.1f}",
                    "-OutputDirectory", str(design))
                from installer_script import write_script
                script = root / "installer.iss"
                write_script(product, index["runtime"], tooling, design, payload, redist, final, script)
                run(str(root / "tools/inno/ISCC.exe"), "/Q", str(script))
            else:
                shutil.copy2(original, final)
            checkpoint("verify-final-payload")
            run(str(innounp), "-t", "-b", "-q", str(final))
            verify = root / "verified"
            run(str(innounp), "-x", "-a", "-m", "-y", "-b", f"-d{verify}", str(final))
            expected = {e["path"]: e for e in product["payloadFiles"]}
            for replacement in product.get("replacements", []):
                expected[replacement["path"]] = replacement
            for addition in product.get("additions", []):
                expected[addition["path"]] = addition
            for relative, entry in expected.items():
                actual = verify / relative
                if not actual.is_file() or actual.stat().st_size != entry["bytes"] or digest(actual) != entry["sha256"]:
                    raise ValueError("Final installer payload inventory mismatch")
            if product["runtimeImports"] and product["needsRepackage"]:
                if digest(verify / "{tmp}/FatBerry-VC_redist.x64.exe") != index["runtime"]["sha256"]:
                    raise ValueError("Final installer lacks the verified runtime")
            checkpoint("install-upgrade-scan-uninstall")
            # Save the exact verified candidate before GUI/installation checks.
            # A later acceptance fix must not force another artwork/package build.
            (final.parent / "candidate.json").write_text(json.dumps({
                "slug": slug, "sha256": digest(final), "sizeBytes": final.stat().st_size,
                "buildInputSha256": product["buildInputSha256"], "payloadFiles": list(expected.values()),
                "validation": {"archiveIntegrity": "passed", "payloadHashes": "passed"}}, indent=2) + "\n")
            for file in (final, final.parent / "candidate.json"):
                transfer("put", f"{output_prefix('windows', slug)}/{file.name}", file)
            from test_windows_install import validate_install
            validator = next(path for path in (root / "pluginval").rglob("pluginval.exe") if path.is_file())
            installation = validate_install(product, final, root, output, validator)
            from test_windows_wizard import validate_wizard
            installation.update(validate_wizard(final, root, output))
            record = {**product["original"], "sizeBytes": final.stat().st_size, "sha256": digest(final),
                      "auditedBuildInputSha256": product["buildInputSha256"], "payloadFiles": list(expected.values()),
                      "validation": {"archiveIntegrity": "passed", "payloadHashes": "passed", "moduleHash": "passed",
                                     "currentBuildInputsMatch": True, **installation, "activation": "pending"},
                      "runtimeIncluded": bool(product["runtimeImports"])}
            (final.parent / "release.json").write_text(json.dumps(record, indent=2) + "\n")
            checkpoint("upload")
            for file in final.parent.iterdir():
                transfer("put", f"{output_prefix('windows', slug)}/{file.name}", file)
            checkpoint("release-acceptance", "candidate")
    except Exception as error:
        with log.open("a", encoding="utf-8") as output:
            output.write(f"\n{type(error).__name__}: {error}\n")
        status["status"] = "failed"
    finally:
        for diagnostic in root.glob("*.log"):
            if diagnostic.name != "build.log":
                transfer("put", f"{output_prefix('windows', slug)}/{diagnostic.name}", diagnostic)
        if log.exists():
            transfer("put", f"{output_prefix('windows', slug)}/build.log", log)
        for diagnostic in (root / "wizard-dumps").glob("*.bmp"):
            transfer("put", f"{output_prefix('windows', slug)}/{diagnostic.name}", diagnostic)
        if (root / "wizard-geometry.json").exists():
            transfer("put", f"{output_prefix('windows', slug)}/wizard-geometry.json", root / "wizard-geometry.json")
        checkpoint(status["stage"], status["status"])
    print(f"{slug}: {status['status']} ({status['stage']}); detailed diagnostics remain private")
    if status["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
