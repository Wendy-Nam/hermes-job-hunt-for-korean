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
    for key in ["resume_projects", "portfolio_projects", "hide_sections", "highlight_terms", "summary_points"]:
        if key in profile and (
            not isinstance(profile[key], list)
            or not all(isinstance(x, str) and x.strip() for x in profile[key])
            or len(set(profile[key])) != len(profile[key])
        ):
            raise ValueError(f"{key} must be a list of unique strings")
    if "summary_points" in profile and not profile["summary_points"]:
        raise ValueError("summary_points must not be empty")
    for key in ["preserve_order", "brief_experiences", "plain_punctuation"]:
        if key in profile and not isinstance(profile[key], bool):
            raise ValueError(f"{key} must be a boolean")
    if "skills" in profile:
        skills = profile["skills"]
        if (not isinstance(skills, list) or not skills
                or any(not isinstance(row, dict) or set(row) != {"label", "value"}
                       or any(not isinstance(row[k], str) or not row[k].strip()
                              for k in ["label", "value"]) for row in skills)
                or len({row["label"] for row in skills}) != len(skills)):
            raise ValueError("skills must contain unique nonempty label/value rows")
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
