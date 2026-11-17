"""Resolve private document profiles; roles and personal content stay outside the engine."""

from pathlib import Path
import yaml


def resolve_profile(job_root, name, language=None, materials_dir=None):
    if not name:
        return materials_dir, language or "ko", {}
    if materials_dir is not None:
        raise ValueError("Use --profile or --materials-dir, not both")
    root = (Path(job_root) / "resume").resolve()
    try:
        data = yaml.safe_load(
            (root / "document_profiles.yaml").read_text(encoding="utf-8")
        )
    except yaml.YAMLError as error:
        raise ValueError("Invalid document_profiles.yaml") from error
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != 1
        or not isinstance(data.get("profiles"), dict)
    ):
        raise ValueError(
            "document_profiles.yaml requires schema_version: 1 and profiles"
        )
    if not all(isinstance(key, str) for key in data["profiles"]):
        raise ValueError("Profile IDs must be strings")
    profile = data["profiles"].get(name)
    if not isinstance(profile, dict):
        raise ValueError(
            f"Unknown document profile: {name}; available: {', '.join(data['profiles'])}"
        )
    if profile.get("language") not in {"ko", "en"} or (
        language and language != profile["language"]
    ):
        raise ValueError("Profile language is missing or differs from --lang")
    relative = profile.get("materials")
    if (
        not isinstance(relative, str)
        or Path(relative).is_absolute()
        or ".." in Path(relative).parts
    ):
        raise ValueError("Profile materials must be a relative path within resume/")
    folder = (root / relative).resolve()
    if not folder.is_relative_to(root):
        raise ValueError("Profile materials leave resume/")
    for key in ["resume_projects", "portfolio_projects", "hide_sections"]:
        if key in profile and (
            not isinstance(profile[key], list)
            or not all(isinstance(x, str) for x in profile[key])
            or len(set(profile[key])) != len(profile[key])
        ):
            raise ValueError(f"{key} must be a list of unique strings")
    if "preserve_order" in profile and not isinstance(profile["preserve_order"], bool):
        raise ValueError("preserve_order must be a boolean")
    return folder, profile["language"], profile


def select_projects(projects, names):
    if names is None:
        return projects
    selected = []
    for name in names:
        matches = [p for p in projects if p["project_name"] == name]
        if len(matches) != 1:
            raise ValueError(f"Project selection must match exactly once: {name}")
        selected.append(matches[0])
    return selected
