#!/usr/bin/env python3
"""Register only job-hunt jobs through the installed Hermes cron API."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import sys


def configure(app, channel, activate=False):
    from cron.jobs import create_job, list_jobs, update_job
    from cron.monitor import check_monitor

    app = Path(app).resolve()
    import yaml
    models = yaml.safe_load((app / "config/models.yaml").read_text())
    sys.path.insert(0, str(app / "bin"))
    from jobmail import mail_skills, check_backend
    if activate and not (models.get("private_model") and models.get("private_provider")):
        raise ValueError("Configure a private model and provider before activating jobs")
    application_skills = mail_skills(app)
    if activate and check_backend(app, app.parents[1])["ready"] is False:
        raise ValueError("Selected mail backend is not ready; run job-hunt mail-check")
    scripts = app.parents[1] / "scripts/job-hunt"
    scripts.mkdir(parents=True, exist_ok=True)
    executable = shlex.quote(sys.executable)
    entry = shlex.quote(str(app / "job-hunt"))
    for name, command in {"collect": "harvest", "queue-monitor": "monitor"}.items():
        p = scripts / f"{name}.sh"
        env = "HERMES_COLLECT_DEADLINE_MIN=8 " if name == "collect" else ""
        p.write_text(f"#!/bin/sh\nexec env {env}{executable} {entry} {command}\n")
        p.chmod(0o700)
    origin = {"platform": "discord", "chat_id": str(channel), "thread_id": None}
    specs = [
        dict(name="job-hunt · 수집", schedule="10 8,20 * * *", prompt="",
             script=str(scripts / "collect.sh"), no_agent=True, deliver="local", failure_deliver="origin"),
        dict(name="job-hunt · 공고 판정", schedule="40 8,20 * * *",
             prompt="job-match 스킬로 새 대기열을 최대 12건 검증하라. Rules와 검색 프로필을 먼저 읽어라. career_master.md가 있으면 career-sync --check로 일치 여부를 검증하고 matching_profile.md와 사례 근거로 비교하라. 신규 판정 결과만 한국어로 간결하게 보고하고 대기열이 비었으면 [SILENT]. 메일은 확인하지 않는다.",
             skills=["job-match"], monitor_script=str(scripts / "queue-monitor.sh"), deliver="origin",
             model=models["private_model"], provider=models["private_provider"]),
        dict(name="job-hunt · 메일과 리마인더", schedule="0 10 * * *",
             prompt="config/mail.yaml에서 선택한 백엔드와 job-application 스킬로 채용 메일을 읽기 전용으로 확인하라. google-workspace 선택 시 해당 스킬의 google_api.py를 사용하고 Composio로 전환하지 않는다. 지원 상태와 임박한 액션을 정리하라. Gmail 발송·회신·읽음·라벨·보관·삭제 금지. 노트는 정본 도구로만 갱신한다. 새 소식과 필요한 액션이 없으면 [SILENT].",
             skills=application_skills, deliver="origin", model=models["private_model"], provider=models["private_provider"]),
    ]
    existing = {j["name"]: j for j in list_jobs(include_disabled=True)}
    result = []
    for spec in specs:
        spec.update(origin=origin, workdir=str(app))
        if spec["name"] in existing:
            updates = dict(spec)
            if not activate:
                updates.update(enabled=False, state="paused", paused_at=datetime.now(timezone.utc).isoformat(),
                               paused_reason="Native job-hunt setup verification")
            job = update_job(existing[spec["name"]]["id"], updates)
        else:
            job = create_job(**spec, paused=True, paused_reason="Native job-hunt setup verification")
        if not job:
            raise RuntimeError(f"Cron registration failed: {spec['name']}")
        if job.get("monitor_script"):
            import subprocess
            output = subprocess.check_output([sys.executable, str(app / "job-hunt"), "monitor"], text=True)
            if not output:
                baseline = check_monitor(job)
                if not baseline.ok:
                    raise RuntimeError(baseline.error)
        if activate:
            job = update_job(job["id"], {"enabled": True, "state": "scheduled", "paused_at": None,
                                         "paused_reason": None, "schedule": spec["schedule"]})
        result.append({k: job.get(k) for k in ["id", "name", "enabled", "schedule", "deliver"]})
    (app / "config/cron-jobs.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--app", required=True)
    p.add_argument("--channel", required=True)
    p.add_argument("--activate", action="store_true")
    args = p.parse_args()
    configure(args.app, args.channel, args.activate)
