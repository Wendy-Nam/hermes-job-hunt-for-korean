#!/usr/bin/env python3
"""Install or upgrade managed code; preserve personal settings, notes and shared Hermes files."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

import yaml

SOURCE = Path(__file__).resolve().parents[1]


def install(home: Path, job_root: Path, *, dry_run=False, mail_backend=None, profile_file=None):
    home, job_root = home.resolve(), job_root.resolve()
    app, state = home / "apps/job-hunt", home / "state/job-hunt"
    version = (SOURCE / "VERSION").read_text().strip()
    profile_source = Path(profile_file) if profile_file else SOURCE / "profiles/korean-junior-ai.yaml"
    profile = yaml.safe_load(profile_source.read_text())
    if not isinstance(profile, dict) or not profile.get("keyword_blocks"):
        raise ValueError("Search profile requires nonempty keyword_blocks")
    if (app / "config/paths.yaml").is_file():
        old = yaml.safe_load((app / "config/paths.yaml").read_text())
        if Path(old["job_root"]).resolve() != job_root:
            raise ValueError("다른 job_root로 재설치할 수 없습니다. 기존 자료를 명시적으로 이전하세요.")
    if (app / "config/mail.yaml").is_file() and mail_backend:
        old = yaml.safe_load((app / "config/mail.yaml").read_text())
        if old.get("backend") != mail_backend:
            raise ValueError("Existing mail backend differs; edit config/mail.yaml explicitly")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = home / "backups" / ("job-hunt-" + stamp)
    managed = {}

    def write(path, data, mode=0o644, preserve=False, owned=True, root=home):
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root and p.is_relative_to(root)):
            raise ValueError(f"심볼릭 링크 설치 경로: {path}")
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"설치 루트 밖 경로: {path}")
        if preserve and path.exists():
            return
        if owned:
            managed[str(path.relative_to(home))] = hashlib.sha256(data).hexdigest()
        if path.is_file() and path.read_bytes() == data:
            return
        if dry_run:
            print(f"DRY-RUN: {path}")
            return
        if path.is_file():
            target = backup / path.relative_to(home)
            if not target.resolve().is_relative_to(home):
                raise ValueError("Backup path leaves Hermes home")
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(path, target)
            target.chmod(0o600)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
            f.write(data)
            tmp = Path(f.name)
        tmp.chmod(mode)
        os.replace(tmp, path)

    for area in [app, state]:
        if area.is_symlink() or not area.resolve().is_relative_to(home):
            raise ValueError(f"설치 루트 밖 경로: {area}")
        if not dry_run:
            area.mkdir(parents=True, exist_ok=True)
            area.chmod(0o700)
    bundle = SOURCE / "bundle/job-search"
    for name in ["bin", "scripts"]:
        for p in (bundle / name).rglob("*.py"):
            if "__pycache__" not in p.parts and not p.name.startswith("._"):
                write(app / name / p.relative_to(bundle / name), p.read_bytes())
    for p in (bundle / "skills/job-search").rglob("*"):
        if p.is_file() and p.suffix in [".py", ".json"] and "job-hunter-kit" not in p.parts and "__pycache__" not in p.parts and not p.name.startswith("._"):
            write(app / "adapters" / p.relative_to(bundle / "skills/job-search"), p.read_bytes())
    vault = SOURCE / "bundle/vault/automation/job-hunting"
    for p in vault.rglob("*"):
        if not p.is_file() or p.name.startswith("._"):
            continue
        if p.parts[-2] in ["templates", "resume"]:
            write(app / "assets/vault/automation/job-hunting" / p.relative_to(vault), p.read_bytes())
        rel = p.relative_to(vault)
        if len(rel.parts) == 1 or rel.parts[0] in ["resume", "templates"] or p.name == "면접-리서치-템플릿.md":
            write(job_root / rel, p.read_bytes(), 0o600, preserve=True, owned=False, root=job_root)
    entry = (SOURCE / "native/job-hunt").read_text().replace("#!/usr/bin/env python3", f"#!{sys.executable}", 1)
    write(app / "job-hunt", entry.encode(), 0o755)
    for name in ["public-review.py", "uninstall.py"]:
        write(app / name, (SOURCE / "native" / name).read_bytes())
    write(app / "VERSION", (version + "\n").encode())
    write(app / "config/models.yaml", (SOURCE / "native/models.yaml").read_bytes(), 0o600, preserve=True, owned=False)
    write(app / "config/mail.yaml", yaml.safe_dump({"backend": mail_backend or "none"}).encode(), 0o600, preserve=True, owned=False)
    write(app / "config/paths.yaml", yaml.safe_dump({"job_root": str(job_root), "state_dir": str(state)}).encode(), 0o600, preserve=True, owned=False)
    write(app / "config/search-profile.yaml", profile_source.read_bytes(), 0o600, preserve=True, owned=False)
    for p in (SOURCE / "native/skills").rglob("*.md"):
        if p.name.startswith("._"):
            continue
        content = p.read_text().replace("@APP@", str(app)).replace("@JOB_ROOT@", str(job_root)).replace("@STATE@", str(state))
        write(home / "skills/job-search" / p.relative_to(SOURCE / "native/skills"), content.encode())
    manifest = json.dumps({"version": version, "files": managed}, ensure_ascii=False, indent=2).encode()
    write(app / "config/install-manifest.json", manifest, 0o600, owned=False)
    if not dry_run:
        for name in ["postings", "resume", "research", "interview-reviews"]:
            (job_root / name).mkdir(parents=True, exist_ok=True)
    print(f"{'Preview' if dry_run else 'Installed'} {version}: {app}\nNotes: {job_root}\nState: {state}")
    if backup.exists():
        print(f"Previous managed files: {backup}")
    print(f"Job note writes: {'ON' if (state / '.kit-live').exists() else 'OFF'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--job-root", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mail-backend", choices=["none", "google-workspace", "composio"])
    parser.add_argument("--profile-file", type=Path)
    args = parser.parse_args()
    install(args.home, args.job_root, dry_run=args.dry_run, mail_backend=args.mail_backend, profile_file=args.profile_file)
