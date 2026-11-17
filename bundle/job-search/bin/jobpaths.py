"""One path contract for native installs; retain the original overlay layout for existing users."""
import os
from pathlib import Path

HOME = Path(os.environ.get("HERMES_DATA") or os.environ.get("HERMES_HOME") or "/opt/data").resolve()
APP = Path(__file__).resolve().parents[1]
CONFIG = Path(os.environ.get("JOB_HUNT_CONFIG") or APP / "config/paths.yaml")
NATIVE = CONFIG.is_file()
if (APP / "adapters").is_dir() and not NATIVE:
    raise SystemExit(f"구직 경로 설정 없음: {CONFIG}")

if NATIVE:
    import yaml
    try:
        settings = yaml.safe_load(CONFIG.read_text())
        JOB_ROOT = Path(settings["job_root"])
        STATE_DIR = Path(settings["state_dir"])
        if STATE_DIR.is_symlink() or (HOME / "state").is_symlink():
            raise ValueError("native state directory must not be a symlink")
        if not JOB_ROOT.is_absolute() or not STATE_DIR.is_absolute():
            raise ValueError("job_root and state_dir must be absolute")
        JOB_ROOT = JOB_ROOT.resolve()
        STATE_DIR = STATE_DIR.resolve()
        if STATE_DIR != (HOME / "state/job-hunt").resolve():
            raise ValueError("native state_dir must be HERMES_HOME/state/job-hunt")
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as e:
        raise SystemExit(f"구직 경로 설정 오류: {e}") from e
    PROFILE_PATH = APP / "config/search-profile.yaml"
    if not PROFILE_PATH.is_file():
        raise SystemExit(f"구직 검색 프로필 없음: {PROFILE_PATH}")
    ADAPTERS = APP / "adapters"
    BIN_DIR = APP / "bin"
    NOTE_ROOT = JOB_ROOT
else:
    STATE_DIR = HOME
    NOTE_ROOT = HOME / "wiki"
    JOB_ROOT = NOTE_ROOT / "automation/job-hunting"
    PROFILE_PATH = HOME / "search-profile.yaml"
    ADAPTERS = HOME / "skills/job-search"
    BIN_DIR = HOME / "bin"

POSTINGS = JOB_ROOT / "postings"
if NATIVE and (POSTINGS.is_symlink() or not POSTINGS.resolve().is_relative_to(JOB_ROOT)):
    raise SystemExit("구직 postings 경로가 정본 폴더 밖을 가리킵니다")
