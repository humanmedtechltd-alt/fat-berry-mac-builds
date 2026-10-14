#!/usr/bin/env python3
"""Always persist private diagnostics, including when GitHub cancels a job."""
import datetime
import json
from pathlib import Path
import sys
from build_product import transfer, output_prefix

root = Path("work")
if root.is_dir():
    status_path = root / "status.json"
    if status_path.is_file():
        status = json.loads(status_path.read_text())
        slug = status["slug"]
        prefix = output_prefix(status.get("platform", "macos"), slug)
        if status.get("status") == "running":
            status.update(status="interrupted", updatedAt=datetime.datetime.now(datetime.timezone.utc).isoformat())
            status_path.write_text(json.dumps(status) + "\n")
        if (root / "build.log").is_file():
            transfer("put", f"{prefix}/build.log", root / "build.log")
        transfer("put", f"{prefix}/status.json", status_path)
