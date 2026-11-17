"""Select one mail backend. Never switch accounts or providers after an authentication failure."""
from pathlib import Path
import re
import subprocess
import sys

import yaml

BACKENDS = ("none", "google-workspace", "composio")


def read_mail_config(app):
    path = Path(app) / "config/mail.yaml"
    config = yaml.safe_load(path.read_text()) if path.exists() else {"backend": "none"}
    if not isinstance(config, dict) or config.get("backend") not in BACKENDS:
        raise ValueError("mail.backend must be none, google-workspace, or composio")
    return config


def mail_skills(app):
    backend = read_mail_config(app)["backend"]
    return (["google-workspace"] if backend == "google-workspace" else []) + ["job-application"]


def check_backend(app, home):
    backend = read_mail_config(app)["backend"]
    if backend == "none":
        return {"backend": backend, "ready": True, "mode": "notes-only"}
    if backend == "composio":
        return {"backend": backend, "ready": None, "mode": "Hermes MCP", "check": "Verify the selected Gmail connection in Hermes"}
    setup = Path(home) / "skills/productivity/google-workspace/scripts/setup.py"
    if not setup.is_file():
        return {"backend": backend, "ready": False, "error": "google-workspace skill is not installed"}
    result = subprocess.run([sys.executable, str(setup), "--check"], capture_output=True, text=True, timeout=60)
    ready = result.returncode == 0 and bool(re.search(r"^AUTHENTICATED\b", result.stdout, re.M))
    return {"backend": backend, "ready": ready, "mode": "google_api.py", "error": None if ready else "Google Workspace authentication check failed"}
