#!/usr/bin/env python3
"""Archive unchanged managed code only. Personal settings, notes and state remain."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil


def uninstall(home, dry_run=False):
    home = Path(home).resolve()
    manifest = home / "apps/job-hunt/config/install-manifest.json"
    if any(p.is_symlink() for p in [manifest, *manifest.parents] if p.is_relative_to(home)):
        raise ValueError("Symlink in installation manifest path")
    data = json.loads(manifest.read_text())
    cron = home / "cron/jobs.json"
    if cron.exists():
        jobs = json.loads(cron.read_text())
        jobs = jobs.get("jobs", []) if isinstance(jobs, dict) else jobs
        active = [j["id"] for j in jobs if j.get("name", "").startswith("job-hunt ·") and j.get("enabled", True)]
        if active and not dry_run:
            raise ValueError(f"Pause job-hunt cron jobs before removing code: {active}")
    paths = []
    for rel, digest in data["files"].items():
        if not rel.startswith(("apps/job-hunt/", "skills/job-search/")) or rel.startswith("apps/job-hunt/config/"):
            raise ValueError(f"Unmanaged path in manifest: {rel}")
        if ".." in Path(rel).parts or Path(rel).is_absolute():
            raise ValueError(f"Unsafe manifest path: {rel}")
        p = home / rel
        if any(part.is_symlink() for part in [p, *p.parents] if part.is_relative_to(home)) or not p.resolve().is_relative_to(home):
            raise ValueError(f"Unsafe managed path: {rel}")
        if p.exists():
            if hashlib.sha256(p.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Locally changed code; preserve it before uninstalling: {rel}")
            paths.append(p)
    target = home / "backups" / ("job-hunt-uninstall-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    if any(p.is_symlink() for p in [target, *target.parents] if p.is_relative_to(home)):
        raise ValueError("Symlink in backup path")
    for p in paths + [manifest]:
        if dry_run:
            print(f"DRY-RUN archive: {p}")
            continue
        dst = target / p.relative_to(home)
        dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.move(str(p), dst)
    print(f"{'Would archive' if dry_run else 'Archived'} {len(paths)} managed files; notes, settings and state preserved.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    uninstall(args.home, args.dry_run)
