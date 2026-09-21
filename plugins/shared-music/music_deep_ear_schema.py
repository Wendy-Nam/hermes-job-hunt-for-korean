"""Version constants shared between the main-process bridge (music_deep_ear.py,
Python 3.13, no heavy deps importable) and the isolated-venv worker
(music_deep_ear_worker.py, torch/demucs/librosa). Kept dependency-free on
purpose so both sides can import it without pulling in the other side's
environment."""
from __future__ import annotations

SCHEMA_VERSION = "1.0.0"
ANALYZER_VERSION = "demucs-htdemucs+librosa-0.11-1.0.0"
