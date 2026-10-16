"""Preserve a truthful terminal activation result after cancellation or timeout."""
import json
import os
from pathlib import Path
from build_product import transfer

work = Path("work")
index = work / "index.json"
if index.is_file():
    configuration = json.loads(index.read_text())
    platform = os.environ["ACCEPTANCE_PLATFORM"]
    status_file = work / "status.json"
    if not status_file.exists():
        status_file.write_text(json.dumps({"status": "interrupted", "platform": platform,
            "stage": "production-activation", "leaseId": configuration["platforms"][platform]["lease"]["leaseId"],
            "slug": configuration["product"]["slug"], "inputIndexSha256": os.environ["INPUT_INDEX_SHA256"]}))
    remote = f"output/diagnostics/{os.environ['GITHUB_RUN_ID']}/activation/{platform}"
    for path in [*work.glob("*.log"), *work.glob("*-result.json"), status_file]:
        transfer("put", f"{remote}/{path.name}", path)
    (work / "private-lease.json").unlink(missing_ok=True)
