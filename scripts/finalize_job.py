#!/usr/bin/env python3
"""Always persist private diagnostics, including when GitHub cancels a job."""
import datetime
import json
from pathlib import Path
import sys
from build_product import transfer

root = Path("work")
if root.is_dir():
    status_path = root / "status.json"
    if status_path.is_file():
        status = json.loads(status_path.read_text())
        slug = status["slug"]
        prefix = f"output/windows/{slug}" if status.get("platform") == "windows" else f"output/{slug}"
        if status.get("status") == "running":
            status.update(status="interrupted", updatedAt=datetime.datetime.now(datetime.timezone.utc).isoformat())
            status_path.write_text(json.dumps(status) + "\n")
        if (root / "build.log").is_file():
            transfer("put", f"{prefix}/build.log", root / "build.log")
        transfer("put", f"{prefix}/status.json", status_path)
