#!/usr/bin/env python3
"""Real installation, upgrade, scan and uninstall on disposable hosted Windows."""
from __future__ import annotations
import ctypes
import json
import os
from pathlib import Path
import re
import subprocess
import winreg

from build_product import digest


def validate_install(product: dict, installer: Path, work: Path, log, pluginval: Path, before_scan=None) -> dict:
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("FATBERRY_RUNNER_ENVIRONMENT") != "github-hosted"
            or os.environ.get("RUNNER_OS") != "Windows"):
        raise ValueError("Installation tests may only change a disposable GitHub-hosted Windows runner")

    def run(*args: str, permitted: tuple[int, ...] = (0,), timeout: int = 600) -> int:
        log.write("CHECK: " + " ".join(args) + "\n")
        log.flush()
        process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
        try:
            result = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], stdout=log, stderr=log, timeout=30, check=False)
            process.wait(timeout=30)
            raise
        if result not in permitted:
            raise ValueError(f"Windows installer test failed with exit {result}")
        return result

    def uninstall_entries():
        # Microsoft's x64 Burn bundle can register its bootstrapper in the
        # 32-bit registry view. The product's own stable AppId remains in x64.
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            yield from uninstall_view(view)

    def uninstall_view(view):
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0,
                            winreg.KEY_READ | view) as root:
            for index in range(winreg.QueryInfoKey(root)[0]):
                try:
                    name = winreg.EnumKey(root, index)
                    with winreg.OpenKey(root, name) as key:
                        values = {}
                        for field in ("DisplayName", "DisplayVersion", "QuietUninstallString", "UninstallString"):
                            try:
                                values[field] = winreg.QueryValueEx(key, field)[0]
                            except FileNotFoundError:
                                pass
                    yield name, values
                except FileNotFoundError:
                    continue

    def argv(command: str) -> list[str]:
        count = ctypes.c_int()
        split = ctypes.windll.shell32.CommandLineToArgvW
        split.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
        split.restype = ctypes.POINTER(ctypes.c_wchar_p)
        values = split(command, ctypes.byref(count))
        if not values:
            raise ValueError("Invalid registered uninstaller command")
        try:
            return [values[i] for i in range(count.value)]
        finally:
            free = ctypes.windll.kernel32.LocalFree
            free.argtypes = [ctypes.c_void_p]
            free.restype = ctypes.c_void_p
            free(values)

    system = Path(os.environ["SystemRoot"]) / "System32"
    runtime_names = product["runtimeImports"]
    before = [name for name in runtime_names if (system / name).exists()]
    if runtime_names:
        cache = (Path(os.environ["ProgramData"]) / "Package Cache").resolve()
        for _, item in list(uninstall_entries()):
            title = item.get("DisplayName", "")
            if (not title.startswith("Microsoft Visual C++") or "(x64)" not in title
                    or not item.get("DisplayVersion", "").startswith("14.")):
                continue
            command = item.get("QuietUninstallString") or item.get("UninstallString")
            if not command:
                continue
            executable = Path(argv(command)[0]).resolve()
            if (executable.name.lower() != "vc_redist.x64.exe" or not executable.is_relative_to(cache)
                    or not executable.is_file()):
                continue
            # Only the actual Microsoft-signed cached v14 x64 bundle is eligible.
            quoted = str(executable).replace("'", "''")
            run("pwsh.exe", "-NoProfile", "-Command",
                f"$s=Get-AuthenticodeSignature -LiteralPath '{quoted}';if($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'Microsoft Corporation'){{exit 1}}")
            run(str(executable), "/uninstall", "/quiet", "/norestart", permitted=(0, 3010, 1605))
    absent_before_install = all(not (system / name).exists() for name in runtime_names)
    log.write("RUNTIME BEFORE INSTALL: " + json.dumps({name: (system / name).exists() for name in runtime_names}) + "\n")
    log.flush()
    constants = {
        "autopf": os.environ["ProgramW6432"], "autopf64": os.environ["ProgramW6432"],
        "commoncf64": os.environ["CommonProgramW6432"], "commoncf": os.environ["CommonProgramW6432"],
        "commonappdata": os.environ["ProgramData"], "userappdata": os.environ["APPDATA"],
        "localappdata": os.environ["LOCALAPPDATA"], "userdocs": str(Path.home() / "Documents"),
    }
    setup = dict(line.split("=", 1) for line in product["setup"] if "=" in line)
    def resolve(value: str) -> Path:
        def replace(match):
            name = match[1].lower()
            if name not in constants:
                raise ValueError(f"Installer test needs an explicit constant adapter: {name}")
            return constants[name]
        return Path(re.sub(r"\{([A-Za-z0-9]+)\}", replace, value))
    constants["app"] = str(resolve(setup["DefaultDirName"]))
    files = {entry["path"]: entry for entry in product["payloadFiles"]}
    for entry in product.get("replacements", []):
        files[entry["path"]] = entry
    for entry in product.get("additions", []):
        files[entry["path"]] = entry
    installed = {}
    for relative, entry in files.items():
        destination = resolve(relative.replace("/", "\\"))
        # Endless Mistakes deliberately promotes its staging bundle after install.
        staged = "." + product["vst3Bundle"] + ".installing"
        destination = Path(str(destination).replace("\\" + staged + "\\", "\\" + product["vst3Bundle"] + "\\"))
        if destination.exists():
            raise ValueError("Runner contains pre-existing product files; clean content test cannot proceed")
        installed[destination] = entry
    options = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-")
    run(str(installer), *options, f"/LOG={work / 'install.log'}", permitted=(0, 3010), timeout=1200)
    def verify():
        for path, entry in installed.items():
            if not path.is_file() or path.stat().st_size != entry["bytes"] or digest(path) != entry["sha256"]:
                raise ValueError("Installed file hash differs from the expected product payload")
    verify()
    if not all((system / name).is_file() for name in runtime_names):
        raise ValueError("Required Visual C++ runtime DLLs remain absent after installation")
    module = next(path for path in installed if path.name.endswith(".vst3") and path.is_file())
    bundle = next(parent for parent in module.parents if parent.name.endswith(".vst3"))
    if before_scan is not None:
        before_scan(bundle)
    run(str(pluginval), "--strictness-level", "5", "--validate", str(bundle), timeout=600)
    run(str(installer), *options, f"/LOG={work / 'reinstall.log'}", permitted=(0, 3010), timeout=1200)
    verify()
    app_id = setup["AppId"].strip("{}")
    match = next((values for name, values in uninstall_entries() if name.lower() == ("{" + app_id + "}_is1").lower()), None)
    if not match:
        raise ValueError("Installed product's existing uninstall identity was not retained")
    uninstaller = Path(argv(match.get("QuietUninstallString") or match["UninstallString"])[0]).resolve()
    allowed_roots = [Path(constants["app"]).resolve()]
    if setup.get("UninstallFilesDir"):
        allowed_roots.append(resolve(setup["UninstallFilesDir"]).resolve())
    if not any(uninstaller.is_relative_to(root) for root in allowed_roots):
        raise ValueError("Product uninstaller resolves outside its declared installation directories")
    run(str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", permitted=(0, 3010))
    if module.exists():
        raise ValueError("Uninstall left the product module installed")
    return {"cleanInstall": "passed", "installedPayloadHashes": "passed", "reinstall": "passed", "uninstall": "passed",
            "pluginval": "passed-x64-level5", "runtimeBeforeTest": before,
            "runtimeBootstrap": "passed-from-absent" if runtime_names and absent_before_install else "not-required" if not runtime_names else "pending-runtime-remained-present"}
