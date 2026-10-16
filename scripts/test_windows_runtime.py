"""Prove the actual EXE bootstraps v14 in a fresh Windows Server Core container."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from build_product import digest, transfer


CHECK = r'''
$ErrorActionPreference = 'Stop'
$inputData = Get-Content -LiteralPath C:\acceptance\request.json -Raw | ConvertFrom-Json
$systemDir = Join-Path $env:SystemRoot 'System32'
$before = @($inputData.runtimeImports | Where-Object { Test-Path -LiteralPath (Join-Path $systemDir $_) })
if ($before.Count) { throw 'The container already contains required runtime DLLs; absence cannot be proved.' }
$installer = 'C:\acceptance\installer-win.exe'
if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant() -ne $inputData.sha256) { throw 'Candidate changed.' }
$constants = @{
  autopf=$env:ProgramW6432; autopf64=$env:ProgramW6432;
  commoncf=$env:CommonProgramW6432; commoncf64=$env:CommonProgramW6432;
  commonappdata=$env:ProgramData; localappdata=$env:LOCALAPPDATA;
  userappdata=$env:APPDATA; userdocs=(Join-Path $env:USERPROFILE 'Documents')
}
function Resolve-Destination([string]$value) {
  return [regex]::Replace($value, '\{([A-Za-z0-9]+)\}', {
    param($m)
    $key = $m.Groups[1].Value.ToLowerInvariant()
    if (!$constants.ContainsKey($key) -or !$constants[$key]) { throw ('Unknown installer constant: ' + $key) }
    return $constants[$key]
  })
}
$constants.app = Resolve-Destination $inputData.defaultDirName
$expected = @{}
foreach ($file in $inputData.payloadFiles) {
  $target = Resolve-Destination ($file.path.Replace('/', '\'))
  $target = $target.Replace(('\.' + $inputData.vst3Bundle + '.installing\'), ('\' + $inputData.vst3Bundle + '\'))
  if (Test-Path -LiteralPath $target) { throw 'A product file already exists in the fresh container.' }
  $expected[$target] = $file
}
$p = Start-Process -FilePath $installer -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-','/LOG=C:\acceptance\runtime-install.log' -Wait -PassThru
if ($p.ExitCode -notin @(0,3010)) { throw ('Actual installer failed: ' + $p.ExitCode) }
foreach ($target in $expected.Keys) {
  $file = $expected[$target]
  if (!(Test-Path -LiteralPath $target -PathType Leaf) -or (Get-Item -LiteralPath $target).Length -ne $file.bytes -or
      (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.sha256) { throw 'Installed payload differs from candidate inventory.' }
}
foreach ($dll in $inputData.runtimeImports) {
  if (!(Test-Path -LiteralPath (Join-Path $systemDir $dll))) { throw 'Actual installer left a required runtime DLL absent.' }
}
$runtime = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64'
if ($runtime.Installed -ne 1) { throw 'The Microsoft x64 runtime is not registered.' }
@{
  runtimeBootstrap='passed-from-absent-container'; runtimeVersion=$runtime.Version;
  runtimeBootstrapOS=[Environment]::OSVersion.Version.ToString(); runtimeBootstrapDesktop=$false;
  installedPayloadHashes='passed'; installerSha256=$inputData.sha256
} | ConvertTo-Json | Set-Content -LiteralPath C:\acceptance\result.json -Encoding UTF8
'''


def validate_runtime(request: dict, installer: Path, work: Path, log) -> dict:
    if (os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Windows"
            or os.environ.get("FATBERRY_RUNNER_ENVIRONMENT") != "github-hosted"):
        raise ValueError("Container acceptance requires a disposable hosted Windows runner")
    if not request["runtimeImports"]:
        return {"runtimeBootstrap": "not-required"}
    image = request["containerImage"]
    if not re.fullmatch(r"mcr\.microsoft\.com/windows/servercore@sha256:[0-9a-f]{64}", image):
        raise ValueError("The Microsoft base image must be pinned by digest")
    if digest(installer) != request["sha256"]:
        raise ValueError("Runtime test candidate hash mismatch")
    if shutil.disk_usage(work).free < 12 * 1024**3:
        raise ValueError("Runner needs 12 GiB free for the disposable Windows container")

    def run(*args, timeout=300):
        process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
        try:
            result = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], stdout=log, stderr=log, timeout=30, check=False)
            raise
        if result:
            raise subprocess.CalledProcessError(result, args)

    run("docker", "info")
    for attempt in range(3):
        try:
            run("docker", "pull", image, timeout=900)
            break
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    staging = work / "runtime-container"
    staging.mkdir()
    shutil.copy2(installer, staging / "installer-win.exe")
    (staging / "request.json").write_text(json.dumps(request), encoding="utf-8")
    (staging / "check.ps1").write_text(CHECK, encoding="utf-8-sig")
    name = "fatberry-runtime-" + os.environ["GITHUB_RUN_ID"] + "-" + request["slug"]
    try:
        run("docker", "run", "--rm", "--name", name, "--isolation=process", "--network=none",
            "--mount", fr"type=bind,source={staging.resolve()},target=C:\acceptance", image,
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", r"C:\acceptance\check.ps1", timeout=1200)
        result = json.loads((staging / "result.json").read_text(encoding="utf-8-sig"))
        if result.get("runtimeBootstrap") != "passed-from-absent-container" or result.get("installerSha256") != request["sha256"]:
            raise ValueError("The isolated runtime test did not prove this candidate")
        result["containerImage"] = image
        return result
    finally:
        subprocess.run(["docker", "rm", "--force", name], stdout=log, stderr=log, timeout=60, check=False)


def main():
    slug = os.environ["PRODUCT_SLUG"]
    index_sha = os.environ["INPUT_INDEX_SHA256"]
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) or not re.fullmatch(r"[0-9a-f]{64}", index_sha):
        raise ValueError("Invalid private runtime acceptance request")
    work = Path("work").resolve()
    work.mkdir()
    remote = f"output/diagnostics/{os.environ['GITHUB_RUN_ID']}/runtime/{slug}"
    result = {"slug": slug, "status": "failed", "stage": "runtime-bootstrap", "inputIndexSha256": index_sha}
    try:
        transfer("get", f"input/windows-acceptance-{index_sha}.json", work / "request-index.json")
        if digest(work / "request-index.json") != index_sha:
            raise ValueError("Acceptance index hash mismatch")
        index = json.loads((work / "request-index.json").read_text())
        request = next(p for p in index["products"] if p["slug"] == slug)
        transfer("get", "input/" + request["file"], work / "installer-win.exe")
        result.update(installerSha256=request["sha256"], buildInputSha256=request["buildInputSha256"], sourceRun=request["sourceRun"])
        with (work / "runtime.log").open("w", encoding="utf-8") as log:
            result["validation"] = validate_runtime(request, work / "installer-win.exe", work, log)
        result["status"] = "passed"
    except Exception as error:
        with (work / "runtime.log").open("a", encoding="utf-8") as log:
            log.write(f"\n{type(error).__name__}: {error}\n")
    finally:
        (work / "status.json").write_text(json.dumps(result, indent=2) + "\n")
        for path in [*work.glob("*.log"), *work.glob("runtime-container/*.log"), *work.glob("runtime-container/result.json"), work / "status.json"]:
            transfer("put", f"{remote}/{path.name}", path)
    print(f"{slug}: {result['status']}; isolated Windows runtime diagnostics are private")
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
