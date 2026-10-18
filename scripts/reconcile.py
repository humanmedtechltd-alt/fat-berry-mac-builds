#!/usr/bin/env python3
"""Event-driven private build report. Does not consume API/model credits.

Runner uploads are untrusted input. This process reports failures and validates
candidate metadata and bytes. Customer publication requires the separate
trusted publication gate; a compiler success alone is never a release.
"""
from __future__ import annotations
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from compilation_identity import fingerprint


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".next")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def status_path(root: Path, platform: str, slug: str, input_index: str | None = None) -> Path:
    paths = [p for p in (root / "output/runs").glob(f"*/*/{platform}/{slug}/status.json")
             if p.parts[-5].isdigit() and p.parts[-4].isdigit()]
    if input_index:
        paths = [p for p in paths if json.loads(p.read_text()).get('inputIndexSha256') == input_index]
    if paths:
        return max(paths, key=lambda p: (int(p.parts[-5]), int(p.parts[-4])))
    return root / "output" / ("windows" if platform == "windows" else "") / slug / "status.json"


def inspect(root: Path, product: dict) -> dict:
    slug = product["slug"]
    row = {"slug": slug, "name": product["name"], "status": "queued", "stage": "awaiting-runner"}
    if product.get("blockedReason"):
        row.update(status="blocked", stage=product["blockedReason"])
        return row
    path = status_path(root, "macos", slug)
    if not path.exists():
        return row
    status = json.loads(path.read_text())
    if status.get("sourceSnapshot") != product["sourceSnapshot"]:
        row.update(stage="awaiting-current-snapshot")
        return row
    if product.get("compilationFingerprint") and status.get("compilationFingerprint") != product["compilationFingerprint"]:
        row.update(stage="awaiting-current-dependencies")
        return row
    for field in ("status", "stage", "runUrl", "updatedAt"):
        if isinstance(status.get(field), str):
            row[field] = status[field]
    if row["status"] != "passed":
        return row
    release_path = path.parent / "release.json"
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


def inspect_windows(root: Path, product: dict) -> dict:
    slug = product["slug"]
    row = {"slug": slug, "name": product["name"], "platform": "windows", "status": "queued", "stage": "awaiting-runner"}
    path = status_path(root, "windows", slug, product.get("inputIndexSha256"))
    if not path.exists():
        return row
    status = json.loads(path.read_text())
    if product.get("inputIndexSha256") and status.get("inputIndexSha256") != product["inputIndexSha256"]:
        row.update(stage="awaiting-current-packaging")
        return row
    if status.get("buildInputSha256") != product["buildInputSha256"]:
        return row
    for field in ("status", "stage", "runUrl", "updatedAt"):
        if isinstance(status.get(field), str):
            row[field] = status[field]
    if row["status"] != "candidate":
        return row
    release = json.loads((path.parent / "release.json").read_text())
    if (release.get("auditedBuildInputSha256") != product["buildInputSha256"]
            or release.get("vst3Sha256") != product.get("compiled", {}).get("moduleSha256", product["original"]["vst3Sha256"])
            or any(release.get(field) != product["original"][field] for field in
                   ("slug", "productId", "version", "keygenProductId", "keygenPolicyId", "licensingClientCommit"))):
        raise ValueError("Windows candidate differs from the audited product identity")
    artifact = path.parent / "installer-win.exe"
    stats = artifact.stat()
    proof = {"inode": stats.st_ino, "mtimeNs": stats.st_mtime_ns, "size": stats.st_size, "sha256": release["sha256"]}
    proof_path = path.parent / ".verified.json"
    prior = json.loads(proof_path.read_text()) if proof_path.exists() else None
    if stats.st_size != release["sizeBytes"]:
        raise ValueError("Windows candidate size mismatch")
    if proof != prior:
        with artifact.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != release["sha256"]:
                raise ValueError("Windows candidate checksum mismatch")
        atomic_json(proof_path, proof)
    row.update(status="candidate-verified", stage="release-acceptance", sha256=release["sha256"], validation=release.get("validation", {}))
    return row


def attach_production_result(root: Path, row: dict) -> dict:
    if row.get('status') != 'candidate-verified' or not row.get('sha256'):
        return row
    paths = sorted((root / 'output/diagnostics').glob('*/activation/' + row['platform'] + '/status.json'),
                   key=lambda value: int(value.parts[-4]) if value.parts[-4].isdigit() else -1, reverse=True)
    for path in paths:
        result = json.loads(path.read_text())
        if result.get('slug') != row['slug'] or result.get('installerSha256') != row['sha256']:
            continue
        row['productionAcceptance'] = {field: result.get(field) for field in ('status', 'stage', 'runUrl', 'inputIndexSha256', 'validation')}
        if result.get('status') in ('failed', 'interrupted'):
            row.update(status='acceptance-failed', stage='production-activation', runUrl=result.get('runUrl'))
        else:
            row['stage'] = 'publication-gate-pending' if result.get('status') == 'passed' else 'production-activation'
        break
    return row


def attach_publication_result(root: Path, row: dict) -> dict:
    # Written by the trusted publisher only after customer GET/hash checks.
    proof = root / 'publication-status.json'
    if not proof.is_file() or not row.get('sha256'):
        return row
    publication = json.loads(proof.read_text()).get('products', {}).get(row['slug'])
    if (publication and publication.get('status') == 'published'
            and publication.get('installers', {}).get(row['platform']) == row['sha256']):
        row.update(status='published', stage='customer-downloads-verified', publication=publication)
    return row


def main() -> None:
    import fcntl
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
                if not product.get("framework"):
                    rows.append({"slug": product["slug"], "name": product["name"], "platform": "macos",
                                 "status": "queued", "stage": "awaiting-explicit-contract-capture"})
                    continue
                # Global dependencies can change while an unselected product's
                # stored fingerprint still describes an older batch.
                product = {**product, "compilationFingerprint": fingerprint(index, product)}
                rows.append({**inspect(root, product), "platform": "macos"})
            except Exception as error:
                rows.append({"slug": product["slug"], "name": product["name"], "status": "rejected",
                             "platform": "macos", "stage": str(error)})
        windows_index = root / "input/windows-index.json"
        if windows_index.exists():
            windows = json.loads(windows_index.read_text())
            windows_sha = hashlib.sha256(windows_index.read_bytes()).hexdigest()
            for product in windows["products"]:
                try:
                    rows.append(inspect_windows(root, {**product, "inputIndexSha256": windows_sha}))
                except Exception as error:
                    rows.append({"slug": product["slug"], "name": product["name"], "status": "rejected",
                                 "platform": "windows", "stage": str(error)})
        rows = [attach_publication_result(root, attach_production_result(root, row)) for row in rows]
        counts = {state: sum(r["status"] == state for r in rows) for state in sorted({r["status"] for r in rows})}
        report = {"updatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  "expectedProducts": 52, "configuredProducts": len({r['slug'] for r in rows}),
                  "platformCoverage": {platform: sum(r.get("platform") == platform for r in rows) for platform in ("windows", "macos")}, "counts": counts,
                  "products": rows, "publication": "Only candidates passing the complete release acceptance gate may be published."}
        atomic_json(root / "report.json", report)
        lines = ["# Fat Berry unattended installer queue", "", f"Updated: {report['updatedAt']}", "",
                 "Existing VPS; standard public GitHub Actions Mac runners; no model polling.", "",
                 f"Configured Windows: {report['platformCoverage']['windows']}/52; Mac: {report['platformCoverage']['macos']}/52. " + ", ".join(f"{k}: {v}" for k, v in counts.items()), ""]
        for row in rows:
            lines.append(f"- **{row['name']} ({row['platform']})**: {row['status']} — {row['stage']}" +
                         (f" ([build]({row['runUrl']}))" if row.get("runUrl") else ""))
        temporary = root / "report.md.next"
        temporary.write_text("\n".join(lines) + "\n")
        temporary.chmod(0o600)
        os.replace(temporary, root / "report.md")
        issues = ["# Something you need to do", "", f"Updated automatically: {report['updatedAt']}", "",
                  "Release acceptance is incomplete. Published entries have passed the trusted gate and customer download verification; other candidates remain private.", ""]
        for row in rows:
            if row["status"] in {"failed", "rejected", "blocked", "interrupted", "acceptance-failed"}:
                issues.append(f"- [ ] {row['name']} ({row['platform']}): {row['status']} — {row['stage']}" +
                              (f". Diagnostics: {row['runUrl']}" if row.get("runUrl") else ""))
        atomic_issue = root / "Something you need to do.md.next"
        atomic_issue.write_text("\n".join(issues) + "\n")
        atomic_issue.chmod(0o600)
        os.replace(atomic_issue, root / "Something you need to do.md")
        qa_helper = root / "production_qa_lease.py"
        if qa_helper.is_file():
            import subprocess
            subprocess.run(["/usr/bin/python3", str(qa_helper), str(root), "reap"], timeout=240, check=False)
        print(json.dumps({"configured": len(rows), "counts": counts}))


if __name__ == "__main__":
    main()
