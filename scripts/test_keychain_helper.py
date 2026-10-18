"""Test real helper calls in an isolated, disposable runner keychain."""
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import tarfile
from build_product import transfer, digest


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "macOS":
        raise ValueError("Only disposable hosted Mac runners may run this test")
    root = Path("work").resolve()
    root.mkdir()
    log_path = root / "keychain.log"
    status = "failed"
    try:
        sha = os.environ["INPUT_INDEX_SHA256"]
        transfer("get", "input/index-" + sha + ".json", root / "index.json")
        if digest(root / "index.json") != sha:
            raise ValueError("Index hash mismatch")
        index = json.loads((root / "index.json").read_text())
        item = next(d for d in index["dependencies"] if d["directory"] == "fat-berry-licensing-client")
        archive = root / "client.tar.gz"
        transfer("get", "input/" + item["file"], archive)
        if digest(archive) != item["sha256"]:
            raise ValueError("Source hash mismatch")
        source = root / "client"
        with tarfile.open(archive) as tar:
            tar.extractall(source, filter="data")
        with log_path.open("w") as log:
            lock_test = root / "lock-test"
            subprocess.run(["xcrun", "clang++", "-std=c++20", "-Wall", "-Wextra", "-Werror",
                            "-arch", "arm64", "-arch", "x86_64", "-mmacosx-version-min=11.0",
                            str(source / "tests/PosixRecursiveFileLock.cpp"), "-o", str(lock_test)],
                           check=True, stdout=log, stderr=log, timeout=120)
            for architecture in ("arm64", "x86_64"):
                subprocess.run(["arch", "-" + architecture, str(lock_test)],
                               check=True, stdout=log, stderr=log, timeout=20)
            helper = root / "helper"
            subprocess.run(["xcrun", "clang++", "-std=c++17", "-arch", "arm64", "-arch", "x86_64",
                            "-mmacosx-version-min=11.0", "-framework", "Security", "-framework", "CoreFoundation",
                            str(source / "src/MacKeychainStore.mm"), "-o", str(helper)], check=True, stdout=log, stderr=log, timeout=120)
            old_default = shlex.split(subprocess.check_output(["security", "default-keychain", "-d", "user"], text=True))[0]
            old_search = shlex.split(subprocess.check_output(["security", "list-keychains", "-d", "user"], text=True))
            keychain = root / "isolated.keychain-db"
            password = secrets.token_hex(24)
            def security(*args):
                subprocess.run(["security", *args], check=True, stdout=log, stderr=log, timeout=20)
            def call(path, operation, data=None, expected=0, architecture="arm64"):
                result = subprocess.run(["arch", "-" + architecture, str(path), operation], input=data,
                                        capture_output=True, timeout=15)
                log.write(f"{path.name} {operation} {architecture}: exit {result.returncode}, bytes {len(result.stdout)}\n")
                log.write(result.stderr.decode(errors="replace"))
                log.flush()
                if result.returncode != expected:
                    raise ValueError("Keychain helper operation failed")
                return result.stdout
            try:
                security("create-keychain", "-p", password, str(keychain))
                security("set-keychain-settings", "-lut", "600", str(keychain))
                security("unlock-keychain", "-p", password, str(keychain))
                security("default-keychain", "-d", "user", "-s", str(keychain))
                security("list-keychains", "-d", "user", "-s", str(keychain))
                subprocess.run(["codesign", "--force", "--sign", "-", "--identifier", "com.fatberry.test.first", str(helper)], check=True, stdout=log, stderr=log)
                call(helper, "load", expected=20)
                # Deliberately non-secret test vector; never create a server entitlement.
                vector = bytes(range(64))
                call(helper, "store", vector)
                if call(helper, "load") != vector:
                    raise ValueError("Stored device identity differs")
                other = root / "other-helper"
                shutil.copy2(helper, other)
                subprocess.run(["codesign", "--force", "--sign", "-", "--identifier", "com.fatberry.test.second", str(other)], check=True, stdout=log, stderr=log)
                if call(other, "load") != vector or call(other, "load", architecture="x86_64") != vector:
                    raise ValueError("Identity cannot be shared across hosts/architectures")
                security("lock-keychain", str(keychain))
                call(helper, "load", expected=69)
                security("unlock-keychain", "-p", password, str(keychain))
                if call(helper, "load") != vector:
                    raise ValueError("Locked-keychain denial replaced the device identity")
                status = "passed"
            finally:
                security("default-keychain", "-d", "user", "-s", old_default)
                security("list-keychains", "-d", "user", "-s", *old_search)
                if keychain.exists():
                    security("delete-keychain", str(keychain))
    except Exception as error:
        with log_path.open("a") as log:
            log.write(f"{type(error).__name__}: {error}\n")
    finally:
        prefix = f"output/diagnostics/{os.environ['GITHUB_RUN_ID']}/keychain"
        transfer("put", prefix + "/keychain.log", log_path)
        result = root / "result.json"
        result.write_text(json.dumps({"status": status, "sourceSnapshot": item.get("sourceSnapshot") if 'item' in locals() else None}) + "\n")
        transfer("put", prefix + "/result.json", result)
    print("Keychain helper:", status, "(private diagnostics uploaded)")
    if status != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
