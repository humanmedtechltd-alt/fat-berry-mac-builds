#!/usr/bin/env python3
"""Event-driven private build report. Does not consume API/model credits.

Runner uploads are untrusted input. This process reports failures and validates
candidate metadata and bytes. Customer publication requires the separate
trusted publication gate; a compiler success alone is never a release.
"""
from __future__ import annotations
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".next")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def inspect(root: Path, product: dict) -> dict:
    slug = product["slug"]
    row = {"slug": slug, "name": product["name"], "status": "queued", "stage": "awaiting-runner"}
    if product.get("blockedReason"):
        row.update(status="blocked", stage=product["blockedReason"])
        return row
    path = root / "output" / slug / "status.json"
    if not path.exists():
        return row
    status = json.loads(path.read_text())
    if status.get("sourceSnapshot") != product["sourceSnapshot"]:
        row.update(stage="awaiting-current-snapshot")
        return row
    for field in ("status", "stage", "runUrl", "updatedAt"):
        if isinstance(status.get(field), str):
            row[field] = status[field]
    if row["status"] != "passed":
        return row
    release_path = root / "output" / slug / "release.json"
    if not release_path.is_file():
        raise ValueError("Successful job did not upload a release record")
    release = json.loads(release_path.read_text())
    for field in ("slug", "productId", "version", "sourceSnapshot", "licensingClientCommit", "keygenProductId", "keygenPolicyId"):
        if release.get(field) != product[field]:
            raise ValueError(f"Candidate identity mismatch: {field}")
    if (release.get("platform") != "macos" or release.get("formats") != ["vst3", "au"]
            or release.get("architecture") != "universal" or release.get("fileName") != "installer-mac.dmg"
            or release.get("adHocSigned") is not True):
        raise ValueError("Candidate platform or signing requirements failed")
    artifact = release_path.parent / "installer-mac.dmg"
    if artifact.stat().st_size != release["sizeBytes"]:
        raise ValueError("Candidate installer size mismatch")
    # Cache only by metadata for this immutable result and file; new uploads
    # replace the inode and trigger another full streamed checksum.
    proof_path = release_path.parent / ".verified.json"
    stats = artifact.stat()
    identity = {"inode": stats.st_ino, "mtimeNs": stats.st_mtime_ns, "size": stats.st_size,
                "sha256": release["sha256"], "snapshot": product["sourceSnapshot"]}
    prior = json.loads(proof_path.read_text()) if proof_path.exists() else None
    if prior != identity:
        with artifact.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != release["sha256"]:
            raise ValueError("Candidate installer checksum mismatch")
        atomic_json(proof_path, identity)
    row.update(status="candidate-verified", stage="release-acceptance", sha256=release["sha256"],
               bytes=release["sizeBytes"], validation=release.get("validation", {}))
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    with (root / "reconcile.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        index = json.loads((root / "input/index.json").read_text())
        rows = []
        for product in index["products"]:
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", product["slug"]):
                raise ValueError("Invalid configured slug")
            try:
                rows.append(inspect(root, product))
            except Exception as error:
                rows.append({"slug": product["slug"], "name": product["name"], "status": "rejected",
                             "stage": str(error)})
        counts = {state: sum(r["status"] == state for r in rows) for state in sorted({r["status"] for r in rows})}
        report = {"updatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  "expectedProducts": 52, "configuredProducts": len(rows), "counts": counts,
                  "products": rows, "publication": "Only candidates passing the complete release acceptance gate may be published."}
        atomic_json(root / "report.json", report)
        lines = ["# Fat Berry unattended installer queue", "", f"Updated: {report['updatedAt']}", "",
                 "Existing VPS; standard public GitHub Actions Mac runners; no model polling.", "",
                 f"Configured: {len(rows)}/52. " + ", ".join(f"{k}: {v}" for k, v in counts.items()), ""]
        for row in rows:
            lines.append(f"- **{row['name']}**: {row['status']} — {row['stage']}" +
                         (f" ([build]({row['runUrl']}))" if row.get("runUrl") else ""))
        temporary = root / "report.md.next"
        temporary.write_text("\n".join(lines) + "\n")
        temporary.chmod(0o600)
        os.replace(temporary, root / "report.md")
        print(json.dumps({"configured": len(rows), "counts": counts}))


if __name__ == "__main__":
    main()
