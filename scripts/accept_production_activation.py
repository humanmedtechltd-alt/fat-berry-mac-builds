"""Exercise real production activation and installed candidates on disposable VMs."""
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import zipfile

from build_product import digest, transfer


def main():
    platform = os.environ["ACCEPTANCE_PLATFORM"]
    index_sha = os.environ["INPUT_INDEX_SHA256"]
    if platform not in {"macos", "windows"} or not re.fullmatch(r"[0-9a-f]{64}", index_sha):
        raise ValueError("An immutable activation acceptance index is required")
    if os.environ.get("FATBERRY_RUNNER_ENVIRONMENT") != "github-hosted":
        raise ValueError("Production acceptance requires a disposable hosted runner")
    work = Path("work").resolve()
    work.mkdir()
    remote = f"output/diagnostics/{os.environ['GITHUB_RUN_ID']}/activation/{platform}"
    status = {"status": "failed", "platform": platform, "stage": "production-activation",
              "inputIndexSha256": index_sha, "runUrl": f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"}
    mounted = False
    activated = False
    deactivated = False
    try:
        transfer("get", f"input/activation-acceptance-{index_sha}.json", work / "index.json")
        if digest(work / "index.json") != index_sha:
            raise ValueError("Activation input index changed")
        index = json.loads((work / "index.json").read_text())
        selected = index["platforms"][platform]
        status.update(slug=index["product"]["slug"], installerSha256=selected["candidate"]["sha256"], leaseId=selected["lease"]["leaseId"])
        for item in selected["dependencies"]:
            archive = work / item["file"]
            transfer("get", "input/" + item["file"], archive)
            if digest(archive) != item["sha256"]:
                raise ValueError("Activation dependency hash mismatch")
            destination = work / item["directory"]
            destination.mkdir()
            if archive.suffix == ".zip":
                with zipfile.ZipFile(archive) as source:
                    source.extractall(destination)
            else:
                with tarfile.open(archive) as source:
                    source.extractall(destination, filter="data")
            archive.unlink()
        credentials = work / "private-lease.json"
        transfer("get", "input/" + selected["lease"]["file"], credentials)
        if digest(credentials) != selected["lease"]["sha256"]:
            raise ValueError("Private QA credential hash mismatch")
        lease = json.loads(credentials.read_text())
        if datetime.datetime.fromisoformat(lease["expiresAt"]) <= datetime.datetime.now(datetime.timezone.utc):
            raise ValueError("QA lease expired; prepare fresh acceptance inputs")
        key = next(p["licenceKey"] for p in lease["products"] if p["productId"] == index["product"]["productId"])
        candidate = selected["candidate"]
        installer = work / ("installer-mac.dmg" if platform == "macos" else "installer-win.exe")
        transfer("get", "input/" + candidate["file"], installer)
        if digest(installer) != candidate["sha256"]:
            raise ValueError("The actual candidate installer changed")
        with (work / "acceptance.log").open("w", encoding="utf-8") as log:
            def run(args, *, secret=None, timeout=180):
                process = subprocess.Popen(args, stdin=subprocess.PIPE if secret is not None else subprocess.DEVNULL,
                                           stdout=log, stderr=subprocess.STDOUT, start_new_session=platform == "macos")
                try:
                    process.communicate(input=secret, timeout=timeout)
                except subprocess.TimeoutExpired:
                    if platform == "macos":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], stdout=log, stderr=log, timeout=30, check=False)
                    process.wait(timeout=30)
                    raise
                if process.returncode:
                    raise subprocess.CalledProcessError(process.returncode, args)

            config = {**index["product"], "apiOrigin": "https://fatberrydsp.com", "keygenPublicKey": index["keygenPublicKey"]}
            config["knownFailureMessages"] = index.get("knownFailureMessages", [])
            architectures = ["arm64", "x86_64"] if platform == "macos" else ["x64"]
            if platform == "macos":
                cached = selected.get("compiledProbe")
                if cached:
                    if cached.get("probeBuildFingerprint") != selected["probeBuildFingerprint"]:
                        raise ValueError("The compiled production probe has stale source inputs")
                    archive = work / "activation-probe.tar.gz"
                    transfer("get", "input/" + cached["file"], archive)
                    if digest(archive) != cached["sha256"]:
                        raise ValueError("The compiled production probe changed")
                    destination = work / "probe-cache"
                    destination.mkdir()
                    with tarfile.open(archive) as data:
                        if sorted(data.getnames()) != ["FatBerryActivationProbe", "fatberry-keychain-store"]:
                            raise ValueError("Unexpected files in the private production probe")
                        data.extractall(destination, filter="data")
                    probe = destination / "FatBerryActivationProbe"
                else:
                    run([sys.executable, "-m", "venv", str(work / "tools")])
                    run([str(work / "tools/bin/pip"), "install", "cmake==4.2.3", "ninja"], timeout=600)
                    cmake = str(work / "tools/bin/cmake")
                    run([cmake, "-S", str(work / "tooling/acceptance"), "-B", str(work / "probe-build"), "-G", "Xcode",
                     "-DCMAKE_OSX_ARCHITECTURES=arm64;x86_64", "-DCMAKE_OSX_DEPLOYMENT_TARGET=11.0",
                     "-DFB_JUCE_ROOT=" + str(work / "juce"), "-DFB_LICENSING_CLIENT_ROOT=" + str(work / "client"),
                     "-DFATBERRY_LICENSING_SNAPSHOT_SHA256=" + selected["licensingSnapshot"],
                         "-DFATBERRY_SODIUM_MACOS_SHA256=" + index["sodiumSha256"], "-DFATBERRY_LICENSING_BUILD_TESTS=OFF"], timeout=1200)
                    run([cmake, "--build", str(work / "probe-build"), "--config", "Release", "--target", "FatBerryActivationProbe", "--parallel", "3"], timeout=1800)
                    probe = Path((work / "probe-build/probe-Release.txt").read_text())
                for path in (probe.parent / "fatberry-keychain-store", probe):
                    run(["lipo", str(path), "-verify_arch", "arm64", "x86_64"])
                    run(["codesign", "--force", "--sign", "-", str(path)])
                    run(["codesign", "--verify", "--strict", str(path)])
                if "probeBuildFingerprint" in selected and not cached:
                    archive = work / "activation-probe.tar.gz"
                    with tarfile.open(archive, "w:gz", compresslevel=3) as data:
                        data.add(probe, arcname="FatBerryActivationProbe")
                        data.add(probe.parent / "fatberry-keychain-store", arcname="fatberry-keychain-store")
                    metadata = work / "activation-probe.json"
                    metadata.write_text(json.dumps({"probeBuildFingerprint": selected["probeBuildFingerprint"],
                                                    "sha256": digest(archive), "bytes": archive.stat().st_size}) + "\n")
                    for artifact in (archive, metadata):
                        transfer("put", f"{remote}/{artifact.name}", artifact)
                mount = work / "mounted"
                mount.mkdir()
                run(["hdiutil", "attach", str(installer), "-nobrowse", "-readonly", "-mountpoint", str(mount)])
                mounted = True
                packages = list(mount.glob("*.pkg"))
                if len(packages) != 1:
                    raise ValueError("The actual Mac download does not contain exactly one installer")
                for entry in candidate["payloadFiles"]:
                    if (Path("/") / entry["path"]).exists():
                        raise ValueError("Mac acceptance found pre-existing product content")
                run(["sudo", "installer", "-pkg", str(packages[0]), "-target", "/"], timeout=600)
                for entry in candidate["payloadFiles"]:
                    path = Path("/") / entry["path"]
                    if path.stat().st_size != entry["bytes"] or digest(path) != entry["sha256"]:
                        raise ValueError("Actual installed Mac payload differs from the frozen candidate")
                status["validation"] = {"actualPkgCleanInstall": "passed", "installedPayloadHashes": "passed"}
                from macos_delivery_checks import inspect_bundles, exercise_recovery, wait_for_audio_component
                bundles = [Path('/Library/Audio/Plug-Ins/VST3') / index['product']['vst3Bundle'],
                           Path('/Library/Audio/Plug-Ins/Components') / index['product']['auBundle']]
                status['validation']['nestedMachO'] = inspect_bundles(bundles, selected['candidate']['minimumOS'])
                import plistlib
                with (bundles[1] / 'Contents/Info.plist').open('rb') as stream:
                    component = plistlib.load(stream)['AudioComponents'][0]
                # Establish discovery before injecting quarantine. Terminating
                # the asynchronous registrar immediately after filesystem changes
                # can discard its update notifications; observe its state instead.
                wait_for_audio_component(component, architectures[0], log)
                status['validation']['auDiscoveryBeforeQuarantine'] = 'passed'
                status['validation'].update(exercise_recovery(mount, bundles, run))
                for architecture in architectures:
                    wait_for_audio_component(component, architecture, log)
                    run(['arch', '-' + architecture, '/usr/bin/auval', '-v',
                         component['type'], component['subtype'], component['manufacturer']], timeout=600)
                status['validation']['auvalAfterQuarantineRecovery'] = 'passed-arm64-x86_64'
            else:
                probe = work / "activation-probe.exe"
                transfer("get", "input/" + selected["probe"]["file"], probe)
                if digest(probe) != selected["probe"]["sha256"]:
                    raise ValueError("The compiled production probe changed")
                status["validation"] = {}

            def check(action, architecture, plugin_path=None, plugin_format=None):
                name = action + "-" + architecture + ("-" + plugin_format if plugin_format else "")
                current = dict(config)
                current.update(index["hostChecks"])
                if plugin_path:
                    current.update(pluginPath=str(plugin_path), pluginFormat=plugin_format,
                                   instrument=index["instrument"], expectAudioOutput=index["expectAudioOutput"])
                configuration = work / (name + "-config.json")
                configuration.write_text(json.dumps(current))
                report = work / (name + "-result.json")
                command = (["arch", "-" + architecture] if platform == "macos" else []) + [str(probe), str(configuration), action, str(report)]
                run(command, secret=(key + "\n").encode() if action == "activate" else None)
                result = json.loads(report.read_text())
                if result.get("status") != "passed":
                    raise ValueError("The production client/host acceptance did not pass")
                status["validation"][name] = result

            def activate():
                nonlocal activated
                check("activate", architectures[0])
                activated = True
                if platform == "macos":
                    # Diagnose the actual production helper without disclosing
                    # the device secret returned on stdout.
                    import re
                    helper_results = {}
                    for architecture in architectures:
                        result = subprocess.run(["arch", "-" + architecture,
                            str(probe.parent / "fatberry-keychain-store"), "load"],
                            input=b"", capture_output=True, timeout=15)
                        codes = re.findall(rb"Keychain error (-?\d+)", result.stderr)
                        helper_results[architecture] = {"exitCode": result.returncode, "bytes": len(result.stdout),
                            "keychainStatus": int(codes[-1]) if codes else None}
                    (work / "keychain-helper-result.json").write_text(json.dumps(helper_results) + "\n")
                check("verify", architectures[-1])

            if platform == "windows":
                from test_windows_install import validate_install
                from test_windows_wizard import validate_wizard
                validator = next((work / "pluginval").rglob("pluginval.exe"))
                def before_scan(bundle):
                    activate()
                    check("validate-plugin", "x64", bundle, "VST3")
                status["validation"].update(validate_install(selected["productContract"], installer, work, log, validator, before_scan=before_scan))
                status["validation"].update(validate_wizard(installer, work, log))
            else:
                activate()
                for architecture in architectures:
                    for kind, bundle, folder, plugin_format in (("vst3", index["product"]["vst3Bundle"], "VST3", "VST3"),
                                                               ("au", index["product"]["auBundle"], "Components", "AudioUnit")):
                        path = Path("/Library/Audio/Plug-Ins") / folder / bundle
                        run(["codesign", "--verify", "--deep", "--strict", str(path)])
                        check("validate-plugin", architecture, path, plugin_format)
            check("refresh", architectures[-1])
            check("deactivate", architectures[0])
            deactivated = True
            check("verify-denied", architectures[-1])
            if platform == "macos":
                run(["sudo", "installer", "-pkg", str(packages[0]), "-target", "/"], timeout=600)
                for entry in candidate["payloadFiles"]:
                    path = Path("/") / entry["path"]
                    if path.stat().st_size != entry["bytes"] or digest(path) != entry["sha256"]:
                        raise ValueError("Reinstallation changed the product payload")
                status["validation"]["reinstall"] = "passed"
                uninstallers = list(mount.glob("Uninstall *.command"))
                if len(uninstallers) != 1:
                    raise ValueError("The actual Mac download lacks its product-specific uninstaller")
                run(["sudo", "/bin/sh", str(uninstallers[0])], timeout=120)
                if any((Path("/") / entry["path"]).exists() for entry in candidate["payloadFiles"]):
                    raise ValueError("Mac uninstall left installed product payload files")
                status["validation"]["uninstall"] = "passed"
            status["status"] = "passed"
    except Exception as error:
        with (work / "acceptance.log").open("a", encoding="utf-8") as log:
            log.write(f"\n{type(error).__name__}: {error}\n")
    finally:
        if activated and not deactivated:
            try:
                with (work / "acceptance.log").open("a", encoding="utf-8") as log:
                    check("deactivate", architectures[0])
            except Exception:
                status["cleanup"] = "QA lease expiry timer will revoke; runner is disposable"
        if mounted:
            subprocess.run(["hdiutil", "detach", str(work / "mounted")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=False)
        (work / "status.json").write_text(json.dumps(status, indent=2) + "\n")
        for path in [*work.glob("*.log"), *work.glob("*-result.json"), *work.glob("wizard-geometry.json"), work / "status.json"]:
            transfer("put", f"{remote}/{path.name}", path)
        (work / "private-lease.json").unlink(missing_ok=True)
    print(f"Production client and installed {platform} candidate: {status['status']}; diagnostics remain private")
    if status["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
