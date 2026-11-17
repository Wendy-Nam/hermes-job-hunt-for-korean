"""Export a private career master into existing document, matching and search inputs."""

import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

import yaml

KINDS = ("master_resume", "fit_evidence", "role_contexts", "portfolios")
REQUIRED = {
    f"{kind}.{lang}"
    for kind in ("master_resume", "role_contexts", "portfolios")
    for lang in ("ko", "en")
} | {"matching_profile", "search_profile", "document_profiles"}
MARKER = re.compile(
    r"^<!-- career:([a-z_.]+) -->\n(.*?)^<!-- /career:\1 -->\s*$", re.M | re.S
)
SEARCH_KEYS = {
    "limit_per_board",
    "body_fetch_cap",
    "boards",
    "keyword_blocks",
    "filters",
    "track_bonus",
    "company_aliases",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def parse_master(text):
    sections = {}
    for match in MARKER.finditer(text):
        key, value = match.groups()
        if key not in REQUIRED or key in sections:
            raise ValueError(f"Unknown or duplicate career section: {key}")
        sections[key] = value.strip() + "\n"
    if (
        set(sections) != REQUIRED
        or text.count("<!-- career:") != len(sections)
        or text.count("<!-- /career:") != len(sections)
    ):
        raise ValueError(
            f"Incomplete career master: missing {sorted(REQUIRED - sections.keys())}"
        )
    for key in ("search_profile", "document_profiles"):
        try:
            data = yaml.safe_load(sections[key])
        except yaml.YAMLError as error:
            raise ValueError(f"Invalid {key} YAML") from error
        if not isinstance(data, dict):
            raise ValueError(f"{key} must be a mapping")
        if key == "search_profile" and (
            set(data) - SEARCH_KEYS or not data.get("keyword_blocks")
        ):
            raise ValueError("Search export accepts public search criteria only")
        if key == "document_profiles" and (
            data.get("schema_version") != 1
            or not isinstance(data.get("profiles"), dict)
        ):
            raise ValueError("Invalid document profiles")
    return sections


def sync_career(job_root, app, *, write=False):
    root, app = Path(job_root).resolve(), Path(app).resolve()
    resume = root / "resume"
    source = resume / "career_master.md"
    raw = source.read_bytes()
    sections = parse_master(raw.decode("utf-8"))
    # Reuse the document parser so source cases feed resumes, cover episodes and fit evidence identically.
    spec = importlib.util.spec_from_file_location(
        "career_document_parser",
        Path(__file__).with_name("generate-tailored-resume.py"),
    )
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    for lang in ("ko", "en"):
        projects = renderer.parse_projects(sections[f"portfolios.{lang}"])
        episodes = "\n## 4. 에피소드\n"
        evidence = "# 사례별 근거\n\n"
        for no, project in enumerate(projects, 1):
            title = project["project_name"]
            evidence += f"- **C{no:02d} {title}**: {project['impact']} ({project['evidence_note']})\n"
            episodes += f"\n### 에피소드 {no}: {title}\n"
            for field, label in [
                ("situation", "상황"),
                ("problem", "문제"),
                ("approach", "해결"),
                ("impact", "임팩트"),
            ]:
                if project[field]:
                    episodes += f"- **{label}**: {project[field]}\n"
        sections[f"master_resume.{lang}"] += episodes
        sections[f"fit_evidence.{lang}"] = evidence
    plan = {}
    for kind in KINDS:
        for lang in ("ko", "en"):
            plan[resume / "materials" / lang / f"{kind}.md"] = sections[
                f"{kind}.{lang}"
            ].encode()
        plan[resume / f"{kind}.md"] = sections[f"{kind}.ko"].encode()
    for key in ("matching_profile", "document_profiles"):
        plan[resume / (key + (".yaml" if key == "document_profiles" else ".md"))] = (
            sections[key].encode()
        )
    plan[app / "config/search-profile.yaml"] = sections["search_profile"].encode()
    manifest_path = resume / "career-sync-manifest.json"
    for path in [source, manifest_path, *plan]:
        boundary = app if path.is_relative_to(app) else root
        if (
            path.is_symlink()
            or any(p.is_symlink() for p in path.parents if p.is_relative_to(boundary))
            or not path.resolve().is_relative_to(boundary)
        ):
            raise ValueError(f"Career export path escapes its root: {path}")
    manifest = {
        "schema_version": 1,
        "master_sha256": digest(raw),
        "files": {str(p): digest(data) for p, data in plan.items()},
    }
    changed = [
        p for p, data in plan.items() if not p.is_file() or p.read_bytes() != data
    ]
    if not write:
        if (
            changed
            or not manifest_path.is_file()
            or json.loads(manifest_path.read_text()) != manifest
        ):
            raise ValueError(
                "Career exports differ from master; merge edits into career_master.md, then run career-sync --write"
            )
        return {"checked": len(plan), "master_sha256": manifest["master_sha256"]}
    if manifest_path.is_file():
        previous = json.loads(manifest_path.read_text())
        for path in plan:
            old = previous.get("files", {}).get(str(path))
            if (
                old
                and path.exists()
                and digest(path.read_bytes()) not in {old, digest(plan[path])}
            ):
                raise ValueError(
                    f"Generated material was edited separately; merge it into the master first: {path}"
                )
    backup = None
    if changed:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = resume / ".career-sync-backups" / stamp
        if any(
            p.is_symlink() for p in [backup, *backup.parents] if p.is_relative_to(root)
        ):
            raise ValueError("Career backup path is a symlink")
        for path in [*changed, manifest_path]:
            if path.is_file():
                relative = (
                    Path("app") / path.relative_to(app)
                    if path.is_relative_to(app)
                    else Path("notes") / path.relative_to(root)
                )
                target = backup / relative
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copy2(path, target)
                target.chmod(0o600)
    plan[manifest_path] = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
    for path, data in plan.items():
        if path.is_file() and path.read_bytes() == data:
            continue
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            stream.write(data)
            temp = Path(stream.name)
        temp.chmod(0o600)
        os.replace(temp, path)
    return {
        "updated": len(changed),
        "master_sha256": manifest["master_sha256"],
        "backup": str(backup) if backup else None,
    }
