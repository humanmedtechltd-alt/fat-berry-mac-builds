#!/usr/bin/env python3
"""Upgrade test of built Windows installers on a developer PC (run elevated).

Usage: test_windows_upgrade.py INDEX_JSON OUTPUT_DIR PLUGINVAL_EXE RESULTS_DIR SLUG...

For each product: the installer is OUTPUT_DIR/<slug>/installer-win.exe (its hash
must equal candidate.json). Nothing is uninstalled from the PC: no WebView2
Runtime, no Visual C++ runtime, no product. Any installed file the upgrade
would change is first copied to RESULTS_DIR/<slug>/backup. Then the installer
runs silently over the installed product, every installed file is hashed
against the contract, WebView2 and the runtime DLLs must be present, the
WebView2 bootstrapper must not have run (the runtime was present), pluginval
runs at strictness 5 and the product's uninstall entry must be intact.
"""
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import winreg
from pathlib import Path


def digest(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


WEBVIEW_ROOTS = [Path(os.environ["ProgramFiles(x86)"]) / "Microsoft/EdgeWebView/Application",
                 Path(os.environ["LOCALAPPDATA"]) / "Microsoft/EdgeWebView/Application"]


def webview_versions():
    versions = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
                                    0, winreg.KEY_READ | view) as key:
                    version = winreg.QueryValueEx(key, "pv")[0]
                if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", version) and any(
                        (folder / version / "msedgewebview2.exe").is_file() for folder in WEBVIEW_ROOTS):
                    versions.append(version)
            except FileNotFoundError:
                pass
    return sorted(set(versions))


def uninstall_entry(app_id):
    wanted = ("{" + app_id + "}_is1").lower()
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0,
                            winreg.KEY_READ | view) as root:
            for index in range(winreg.QueryInfoKey(root)[0]):
                name = winreg.EnumKey(root, index)
                if name.lower() != wanted:
                    continue
                with winreg.OpenKey(root, name) as key:
                    values = {}
                    for field in ("UninstallString", "QuietUninstallString", "DisplayVersion", "DisplayName"):
                        try:
                            values[field] = winreg.QueryValueEx(key, field)[0]
                        except FileNotFoundError:
                            pass
                    return values
    return None


def argv(command):
    count = ctypes.c_int()
    split = ctypes.windll.shell32.CommandLineToArgvW
    split.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    split.restype = ctypes.POINTER(ctypes.c_wchar_p)
    values = split(command, ctypes.byref(count))
    try:
        return [values[i] for i in range(count.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(values, ctypes.c_void_p))


def test(product, installer, pluginval, results, log):
    def run(*args, permitted=(0,), timeout=1200):
        log.write("CHECK: " + " ".join(map(str, args)) + "\n")
        log.flush()
        code = subprocess.run(list(map(str, args)), stdout=log, stderr=subprocess.STDOUT, timeout=timeout).returncode
        if code not in permitted:
            raise ValueError(f"exit {code}: {args[0]}")
        return code

    constants = {"autopf": os.environ["ProgramW6432"], "autopf64": os.environ["ProgramW6432"],
                 "commoncf64": os.environ["CommonProgramW6432"], "commoncf": os.environ["CommonProgramW6432"],
                 "commonappdata": os.environ["ProgramData"], "userappdata": os.environ["APPDATA"],
                 "localappdata": os.environ["LOCALAPPDATA"], "userdocs": str(Path.home() / "Documents")}
    setup = dict(line.split("=", 1) for line in product["setup"] if "=" in line)

    def resolve(value):
        return Path(re.sub(r"\{([A-Za-z0-9]+)\}", lambda m: constants[m[1].lower()], value))

    constants["app"] = str(resolve(setup["DefaultDirName"]))
    files = {e["path"]: e for e in product["payloadFiles"]}
    for e in product.get("replacements", []) + product.get("additions", []):
        files[e["path"]] = e
    installed = {resolve(p.replace("/", "\\")): e for p, e in files.items()}
    before = {"identical": 0, "different": 0, "absent": 0}
    backup = results / "backup"
    for path, entry in installed.items():
        if not path.is_file():
            before["absent"] += 1
        elif path.stat().st_size == entry["bytes"] and digest(path) == entry["sha256"]:
            before["identical"] += 1
        else:
            before["different"] += 1
            target = backup / path.relative_to(path.anchor)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    webview_before = webview_versions()
    system = Path(os.environ["SystemRoot"]) / "System32"
    runtime_before = {n: (system / n).exists() for n in product["runtimeImports"]}
    install_log = results / "install.log"
    run(installer, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", f"/LOG={install_log}", permitted=(0, 3010))
    for path, entry in installed.items():
        if not path.is_file() or path.stat().st_size != entry["bytes"] or digest(path) != entry["sha256"]:
            raise ValueError("Installed file differs from the contract: " + str(path))
    webview_after = webview_versions()
    minimum = product["webView2Runtime"]["minimumMajor"]
    if not any(int(v.split(".")[0]) >= minimum for v in webview_after):
        raise ValueError("WebView2 Runtime is not present after installation")
    text = install_log.read_text(encoding="utf-8", errors="replace")
    bootstrapper_ran = "FatBerry-WebView2Setup.exe" in text
    if webview_before and bootstrapper_ran:
        raise ValueError("The WebView2 bootstrapper ran although the runtime was present")
    if not all((system / n).is_file() for n in product["runtimeImports"]):
        raise ValueError("Visual C++ runtime DLLs are absent after installation")
    module = next(p for p in installed if p.name.endswith(".vst3") and p.parent.name == "x86_64-win")
    bundle = next(parent for parent in module.parents if parent.name.endswith(".vst3"))
    run(pluginval, "--strictness-level", "5", "--validate", bundle, timeout=900)
    entry = uninstall_entry(setup["AppId"].strip("{}"))
    if not entry:
        raise ValueError("The product's uninstall entry is missing after the upgrade")
    uninstaller = Path(argv(entry.get("QuietUninstallString") or entry["UninstallString"])[0]).resolve()
    roots = [Path(constants["app"]).resolve()] + ([resolve(setup["UninstallFilesDir"]).resolve()] if setup.get("UninstallFilesDir") else [])
    if not any(uninstaller.is_relative_to(r) for r in roots) or not uninstaller.is_file():
        raise ValueError("The registered uninstaller is outside the product's folders")
    # A product that was not installed before the test is removed again, which also tests its uninstaller.
    fresh = before["identical"] == 0 and before["different"] == 0
    if fresh:
        run(uninstaller, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", permitted=(0, 3010))
        if any(path.exists() for path in installed):
            raise ValueError("Uninstall left product payload files installed")
    return {"upgradeInstall" if not fresh else "cleanInstall": "passed", "installedBefore": before,
            "installedPayloadHashes": "passed", "pluginval": "passed-x64-level5", "uninstallEntry": "passed",
            "uninstall": "passed" if fresh else "not-run-developer-pc",
            "webView2Bootstrap": "skipped-runtime-present-developer-pc" if webview_before else "ran-from-absent",
            "webView2BeforeTest": webview_before, "webView2InstalledVersions": webview_after,
            "runtimeBeforeTest": [n for n, present in runtime_before.items() if present],
            "testedOn": "developer PC, upgrade over the installed release (nothing removed)"}


def main():
    index = json.loads(Path(sys.argv[1]).read_text())
    outputs, pluginval, results_root = Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    summary = {}
    for slug in sys.argv[5:]:
        product = next(p for p in index["products"] if p["slug"] == slug)
        folder = outputs / slug
        installer = folder / "installer-win.exe"
        results = results_root / slug
        results.mkdir(parents=True, exist_ok=True)
        with open(results / "test.log", "w", encoding="utf-8") as log:
            try:
                candidate = json.loads((folder / "candidate.json").read_text())
                if digest(installer) != candidate["sha256"]:
                    raise ValueError("installer differs from its candidate record")
                summary[slug] = {"status": "passed", **test(product, installer, pluginval, results, log)}
            except Exception as error:  # Record and continue with the next product.
                log.write(f"\n{type(error).__name__}: {error}\n")
                summary[slug] = {"status": "failed", "error": f"{type(error).__name__}: {error}"}
        (results / "result.json").write_text(json.dumps(summary[slug], indent=2) + "\n")
    (results_root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
