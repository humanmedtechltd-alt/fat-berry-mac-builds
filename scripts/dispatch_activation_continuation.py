"""Run one trusted independent follow-up batch after both platform jobs finish."""
import json
import os
from pathlib import Path
import re
import subprocess

checksum = os.environ["INPUT_INDEX_SHA256"]
kind = os.environ.get("CONTINUATION_KIND", "activation")
if kind not in {"activation", "build"}:
    raise ValueError("Unknown completed workflow kind")
if not re.fullmatch(r"[0-9a-f]{64}", checksum):
    raise SystemExit("Invalid completed acceptance identity")
ssh = ["ssh", "-i", str(Path.home() / ".ssh/fatberry-build"), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
       "-o", "ConnectTimeout=20", os.environ["RUNNER_SSH_HOST"]]
result = subprocess.run(ssh + [f"get input/{kind}-continuation-{checksum}.json"], stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
if result.returncode:
    # A batch without an explicit continuation simply stops. The server's
    # one-shot claim also suppresses duplicate events and manual job retries.
    if b"No such file or directory" not in result.stderr and b"Continuation already claimed" not in result.stderr:
        raise RuntimeError("Private continuation transport failed; no follow-up was dispatched")
    print("No unclaimed continuation is queued")
else:
    plan = json.loads(result.stdout)
    if plan.get("afterIndexSha256") != checksum or plan.get("workflow") != "build.yml":
        raise ValueError("The queued follow-up is not an approved Mac build")
    index = plan["indexSha256"]
    products = plan["products"]
    if (not re.fullmatch(r"[0-9a-f]{64}", index) or not isinstance(products, list) or not 1 <= len(products) <= 52
            or any(not isinstance(p, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", p) for p in products)):
        raise ValueError("Invalid immutable continuation contract")
    # Only the already approved public controller and standard Mac workflow.
    subprocess.run(["gh", "workflow", "run", "build.yml", "--repo", "humanmedtechltd-alt/fat-berry-mac-builds", "--ref", "main",
                    "-f", "products=" + json.dumps(products), "-f", "index_sha256=" + index], check=True, timeout=120)
