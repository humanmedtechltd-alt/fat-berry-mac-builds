"""Exercise the actual interactive wizard and cancel before changing any files."""
import ctypes
from ctypes import wintypes as w
import os
import json
from pathlib import Path
import subprocess
import time


def validate_wizard(installer: Path, work: Path, log) -> dict:
    if (os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Windows"
            or os.environ.get("FATBERRY_RUNNER_ENVIRONMENT") != "github-hosted"):
        raise ValueError("Interactive wizard checks require a disposable hosted Windows runner")
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    user.EnumWindows.argtypes = [callback, w.LPARAM]
    user.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    user.GetClientRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
    user.IsWindowVisible.argtypes = [w.HWND]
    dump = work / "wizard-dumps"
    dump.mkdir()
    process = subprocess.Popen([str(installer), "/SP-", "/SUPPRESSMSGBOXES", "/NORESTART", "/FBSCALE=100",
                                f"/FBDUMP={dump}", f"/LOG={work / 'wizard.log'}"], stdout=log, stderr=log)
    try:
        # The protected child recreates its HWND while applying BorderStyle.
        # Wait for the visible final handle after InitializeWizard completes.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel.OpenProcess.restype = w.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
        kernel.CloseHandle.argtypes = [w.HANDLE]
        class ProcessEntry(ctypes.Structure):
            _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ProcessID", w.DWORD),
                        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", w.DWORD), ("cntThreads", w.DWORD),
                        ("th32ParentProcessID", w.DWORD), ("pcPriClassBase", w.LONG), ("dwFlags", w.DWORD),
                        ("szExeFile", w.WCHAR * 260)]
        kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
        for function in (kernel.Process32FirstW, kernel.Process32NextW):
            function.argtypes = [w.HANDLE, ctypes.POINTER(ProcessEntry)]
        parents = {}
        def refresh_parents():
            handle = kernel.CreateToolhelp32Snapshot(2, 0)
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                entry = ProcessEntry()
                entry.dwSize = ctypes.sizeof(entry)
                present = kernel.Process32FirstW(handle, ctypes.byref(entry))
                while present:
                    parents[entry.th32ProcessID] = entry.th32ParentProcessID
                    present = kernel.Process32NextW(handle, ctypes.byref(entry))
            finally:
                kernel.CloseHandle(handle)
        def belongs(pid):
            parent = pid
            for _ in range(12):
                if parent == process.pid:
                    break
                parent = parents.get(parent, 0)
            else:
                return False
            if parent != process.pid:
                return False
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                text = ctypes.create_unicode_buffer(32768)
                length = w.DWORD(len(text))
                if not kernel.QueryFullProcessImageNameW(handle, 0, text, ctypes.byref(length)):
                    return False
                candidate = Path(text.value).resolve()
                return candidate == installer.resolve() or candidate.is_relative_to(Path(os.environ["TEMP"]).resolve())
            finally:
                kernel.CloseHandle(handle)
        windows = []
        @callback
        def collect(hwnd, unused):
            name = ctypes.create_unicode_buffer(128)
            user.GetClassNameW(hwnd, name, len(name))
            if name.value == "TWizardForm" and user.IsWindowVisible(hwnd):
                pid = w.DWORD()
                user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if belongs(pid.value):
                    windows.append(hwnd)
            return True
        deadline = time.monotonic() + 45
        stable = None
        stable_since = time.monotonic()
        rect = w.RECT()
        while time.monotonic() < deadline and process.poll() is None:
            windows.clear()
            refresh_parents()
            user.EnumWindows(collect, 0)
            current = None
            if len(windows) == 1 and user.GetClientRect(windows[0], ctypes.byref(rect)):
                current = (windows[0], rect.right, rect.bottom)
            if current is not None and current == stable:
                if time.monotonic() - stable_since >= 0.5:
                    break
            else:
                stable, stable_since = current, time.monotonic()
            time.sleep(0.2)
        if len(windows) != 1 or stable is None:
            raise ValueError("The product's interactive wizard did not open uniquely")
        window = windows[0]
        if not user.GetClientRect(window, ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        dimensions = (rect.right, rect.bottom)
        (work / "wizard-geometry.json").write_text(json.dumps({"client": dimensions, "expected": [820, 560]}))
        if process.poll() is not None:
            raise ValueError("The interactive wizard crashed before cancellation")
        user.PostMessageW(window, 0x0010, 0, 0)  # WM_CLOSE uses the actual Cancel path.
        if process.wait(timeout=30) != 2:
            raise ValueError("Wizard cancellation crashed or did not report user cancellation")
        if dimensions != (820, 560):
            raise ValueError(f"The actual wizard client is {dimensions}; expected (820, 560)")
        return {"interactiveWizardOpenAndCancel": "passed"}
    finally:
        if process.poll() is None:
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], stdout=log, stderr=log, timeout=30, check=False)
