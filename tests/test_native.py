"""Native install integration checks. No network or real Hermes home is used."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.notes = self.home / "vaults/work/02_work/job-hunting"
        self.notes.mkdir(parents=True)
        self.app = self.home / "apps/job-hunt"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("HERMES_", "JOB_HUNT_"))}
        self.env["HERMES_HOME"] = str(self.home)
        self.install()

    def install(self):
        p = subprocess.run([sys.executable, str(ROOT / "native/install.py"), "--home", str(self.home),
                            "--job-root", str(self.notes)], capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr)

    def cli(self, *args, ok=True, env=None):
        p = subprocess.run([sys.executable, str(self.app / "job-hunt"), *args],
                           capture_output=True, text=True, env=env or self.env)
        if ok:
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        return p

    def live(self):
        (self.home / "state/job-hunt/.kit-live").touch()

    def test_legacy_live_marker_does_not_allow_job_writes(self):
        (self.home / ".kit-live").touch()
        env = {**self.env, "HERMES_KIT_LIVE": "1"}
        self.cli("stage", "--company", "Example", "--position", "AI 교육 담당자", "--url", "https://example.com/a", env=env)
        self.assertFalse((self.home / "state/job-hunt/.job-staging.json").exists())
        self.assertFalse((self.home / "wiki").exists())

    def test_stage_commit_and_state_sync_use_existing_vault(self):
        self.live()
        self.cli("stage", "--company", "Example", "--position", "AI 교육 담당자", "--url", "https://example.com/a")
        self.cli("commit", "--url", "https://example.com/a", "--fit", "✅ 교육 운영 근거 확인", "--priority", "A")
        note = next((self.notes / "postings").glob("*.md"))
        self.cli("note", str(note), "세부=면접")
        self.assertIn('상태: "in-progress"', note.read_text())
        self.assertIn('세부: "면접"', note.read_text())
        self.assertEqual(json.loads((self.home / "state/job-hunt/.job-staging.json").read_text()), [])
        self.assertFalse((self.home / "wiki").exists())

    def test_note_outside_job_area_and_symlink_are_rejected(self):
        self.live()
        other = self.home / "vaults/work/private.md"
        other.write_text('---\n메모: ""\n---\noriginal\n')
        self.assertNotEqual(self.cli("note", str(other), "메모=bad", ok=False).returncode, 0)
        link = self.notes / "link.md"
        link.symlink_to(other)
        self.assertNotEqual(self.cli("note", str(link), "메모=bad", ok=False).returncode, 0)
        self.assertIn("original", other.read_text())

    def test_install_preserves_preferences_notes_and_shared_noteio(self):
        p = self.app / "config/search-profile.yaml"
        p.write_text(p.read_text() + "\ncustom_value: keep\n")
        note = self.notes / "original.md"
        note.write_text("existing history")
        shared = self.home / "bin/noteio.py"
        shared.parent.mkdir(exist_ok=True)
        shared.write_text("shared daily wiki dependency")
        self.install()
        self.assertIn("custom_value: keep", p.read_text())
        self.assertEqual(note.read_text(), "existing history")
        self.assertEqual(shared.read_text(), "shared daily wiki dependency")

    def test_monitor_empty_queue_is_silent_and_nonempty_is_stable(self):
        self.assertEqual(self.cli("monitor").stdout, "")
        self.live()
        self.cli("stage", "--company", "Example", "--position", "GTM Operations 주니어", "--url", "https://example.com/b")
        first = self.cli("monitor").stdout
        self.assertTrue(first.strip())
        self.assertEqual(first, self.cli("monitor").stdout)

    def test_track_ranking_is_stronger_than_company_tier(self):
        self.live()
        postings = self.notes / "postings"
        scores = []
        for i, (title, tier) in enumerate([("AI 교육 담당자", "C"), ("업무 자동화 PM", "S"), ("GTM RevOps", "S"), ("AI 솔루션 영업", "S")]):
            note = postings / f"rank{i}.md"
            note.write_text(f'---\n회사: "Example"\n포지션: "{title}"\n티어: {tier}\n적합도: "✅ 확인"\n우선도: "0"\n---\n')
            self.cli("rank", "--file", str(note))
            import yaml
            scores.append(float(yaml.safe_load(note.read_text().split("---")[1])["우선도"]))
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(len(set(scores)), 4)

    def test_junior_education_and_automation_pass_but_sales_and_senior_do_not(self):
        self.live()
        titles = ["AI 교육 담당자 신입", "AI Trainer", "업무 자동화 PM 주니어", "GTM RevOps 주니어",
                  "AI 솔루션 영업", "AI 교육 담당자 3년 이상", "AI 데이터 라벨링 트레이너",
                  "AI 교육 영업 담당자", "AI 교육 B2B 세일즈", "AI Automation Sales", "GTM Lead", "Staff AI Trainer", "AI 교육 인턴"]
        for i, title in enumerate(titles):
            self.cli("stage", "--company", f"Example{i}", "--position", title, "--url", f"https://example.com/{i}")
        rows = json.loads((self.home / "state/job-hunt/.job-staging.json").read_text())
        self.assertEqual([r["포지션"] for r in rows], titles[:4])

    def test_missing_native_path_config_fails_closed(self):
        (self.app / "config/paths.yaml").unlink()
        self.assertNotEqual(self.cli("status", ok=False).returncode, 0)
        self.assertFalse((self.home / "wiki").exists())

    def test_installer_rejects_symlink_escape(self):
        outside = self.home / "outside"
        outside.mkdir()
        target = outside / "sentinel.py"
        target.write_text("keep")
        (self.app / "bin/noteio.py").unlink()
        (self.app / "bin/noteio.py").symlink_to(target)
        # A symlink to a path inside the requested home is still not a managed regular file.
        p = subprocess.run([sys.executable, str(ROOT / "native/install.py"), "--home", str(self.home),
                            "--job-root", str(self.notes)], capture_output=True, text=True, env=self.env)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(target.read_text(), "keep")

    def test_state_detail_conflict_is_rejected_without_writes(self):
        self.live()
        p = self.notes / "postings/conflict.md"
        original = '---\n상태: "open"\n세부: "발견"\n---\nkeep\n'
        p.write_text(original)
        self.assertNotEqual(self.cli("note", str(p), "세부=면접", "상태=done", ok=False).returncode, 0)
        self.assertEqual(p.read_text(), original)
        self.assertNotEqual(self.cli("note", str(p), "상태=done", ok=False).returncode, 0)
        self.assertEqual(p.read_text(), original)

    def test_rank_rejects_outside_files_and_preserves_body(self):
        self.live()
        outside = self.home / "private.md"
        original = '---\n회사: "Example"\n우선도: "0"\n---\n'
        outside.write_text(original)
        self.assertNotEqual(self.cli("rank", "--file", str(outside), ok=False).returncode, 0)
        self.assertEqual(outside.read_text(), original)
        note = self.notes / "postings/body.md"
        body = '\n회사: 본문 예시\n우선도: 777\n포지션: 예시 본문\n'
        note.write_text(original + body)
        self.cli("rank", "--file", str(note))
        self.assertTrue(note.read_text().endswith(body))

    def test_missing_profile_and_global_state_path_fail_closed(self):
        import yaml
        paths = self.app / "config/paths.yaml"
        p = yaml.safe_load(paths.read_text())
        p["state_dir"] = str(self.home)
        paths.write_text(yaml.safe_dump(p))
        self.assertNotEqual(self.cli("status", ok=False).returncode, 0)
        p["state_dir"] = str(self.home / "state/job-hunt")
        paths.write_text(yaml.safe_dump(p))
        (self.app / "config/search-profile.yaml").unlink()
        self.assertNotEqual(self.cli("status", ok=False).returncode, 0)

    def test_reinstall_with_different_vault_is_rejected(self):
        other = self.home / "other-vault"
        p = subprocess.run([sys.executable, str(ROOT / "native/install.py"), "--home", str(self.home),
                            "--job-root", str(other)], capture_output=True, text=True, env=self.env)
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse(other.exists())

    def test_documents_use_native_materials_and_default_output(self):
        master = self.notes / "resume/master_resume.md"
        master.write_text('## 1. 기본 정보\n- **이름**: Native Person\n- **한 줄 소개**: Verified education\n')
        self.cli("documents", "--company", "Example", "--position", "AI 교육", "--jd-text", "교육")
        outputs = list((self.notes / "outputs").glob("*.html"))
        self.assertEqual(len(outputs), 2)
        self.assertIn("Native Person", outputs[0].read_text())
        p = self.cli("documents", "--selftest")
        self.assertNotIn("Native Person", p.stdout + p.stderr)
        self.assertFalse((self.home / "wiki").exists())

    def test_documents_can_select_role_materials_without_rewriting_master(self):
        original = (self.notes / 'resume/master_resume.md').read_bytes()
        folder = self.notes / 'resume/variants/education'
        folder.mkdir(parents=True)
        for name in ['master_resume', 'fit_evidence', 'role_contexts', 'portfolios']:
            (folder / (name + '.md')).write_text('- **이름**: Selected Person\n' if name == 'master_resume' else '')
        self.cli('documents', '--materials-dir', str(folder), '--only', 'resume')
        file = next((self.notes / 'outputs').glob('*.html'))
        self.assertIn('Selected Person', file.read_text())
        self.assertEqual((self.notes / 'resume/master_resume.md').read_bytes(), original)
        (folder / 'fit_evidence.md').unlink()
        failed = self.cli('documents', '--materials-dir', str(folder), '--out', str(self.notes / 'missing-output'), ok=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse((self.notes / 'missing-output').exists())

    def test_ats_dispatch_passes_keyword_as_named_option(self):
        target = self.app / "adapters/ats-search/scripts/ats_search.py"
        target.write_text('import argparse\np=argparse.ArgumentParser()\np.add_argument("--keyword", required=True)\np.add_argument("--json", action="store_true")\nprint(p.parse_args().keyword)\n')
        self.assertEqual(self.cli("search", "ats", "AI education").stdout.strip(), "AI education")

    def test_network_outage_is_not_successful_empty_collection(self):
        for p in (self.app / "adapters").rglob("*.py"):
            if p.parent.name == "scripts":
                p.write_text('import sys\nprint("[]")\nprint("network failure",file=sys.stderr)\n')
        result = self.cli("collect", ok=False)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_manual_wanted_search_reuses_existing_proxy_doctor(self):
        doctor = self.home / "bin/proxy-doctor.py"
        doctor.parent.mkdir(exist_ok=True)
        doctor.write_text('print("http://proxy.invalid:8080")\n')
        target = self.app / "adapters/wanted-search/scripts/wanted_search.py"
        target.write_text('import os\nprint(os.environ.get("HTTPS_PROXY")=="http://proxy.invalid:8080")\n')
        self.assertEqual(self.cli("search", "wanted", "AI 교육").stdout.strip(), "True")
        jd = self.app / "adapters/job-match/scripts/fetch_jd.py"
        jd.write_text(target.read_text())
        self.assertEqual(self.cli("jd", "https://www.wanted.co.kr/wd/1").stdout.strip(), "True")

    def test_dry_run_does_not_create_install_or_note_directories(self):
        home = self.home / "preview"
        p = subprocess.run([sys.executable, str(ROOT / "native/install.py"), "--home", str(home),
                            "--job-root", str(home / "vault/jobs"), "--dry-run"], capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse(home.exists())

    def test_upgrade_backs_up_changed_code_and_tracks_only_managed_files(self):
        file = self.app / "bin/priority-recalc.py"
        file.write_text("previous local code")
        self.install()
        copies = list((self.home / "backups").glob("job-hunt-*/apps/job-hunt/bin/priority-recalc.py"))
        self.assertTrue(copies)
        self.assertEqual(copies[-1].read_text(), "previous local code")
        manifest = json.loads((self.app / "config/install-manifest.json").read_text())
        self.assertIn("apps/job-hunt/bin/priority-recalc.py", manifest["files"])
        self.assertNotIn("apps/job-hunt/config/search-profile.yaml", manifest["files"])
        self.assertNotIn(str(self.notes.relative_to(self.home) / "Rules.md"), manifest["files"])

    def test_partial_vault_gets_missing_templates_and_existing_kanban_survives(self):
        self.assertTrue((self.notes / "Rules.md").is_file())
        self.assertIn("tasknotesKanban", (self.notes / "Dashboard.base").read_text())
        (self.notes / "Dashboard.base").write_text("user custom kanban")
        self.install()
        self.assertEqual((self.notes / "Dashboard.base").read_text(), "user custom kanban")

    def test_mail_choice_is_saved_and_reinstallation_keeps_it(self):
        import yaml
        config = self.app / "config/mail.yaml"
        self.assertEqual(yaml.safe_load(config.read_text())["backend"], "none")
        config.write_text("backend: composio\n")
        self.install()
        self.assertEqual(yaml.safe_load(config.read_text())["backend"], "composio")

    def test_uninstall_preserves_notes_settings_state_and_unmanaged_code(self):
        note = self.notes / "keep.md"
        note.write_text("private note")
        extra = self.app / "bin/user-owned.py"
        extra.write_text("user file")
        p = subprocess.run([sys.executable, str(ROOT / "native/uninstall.py"), "--home", str(self.home)],
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse((self.app / "job-hunt").exists())
        self.assertTrue((self.app / "config/search-profile.yaml").exists())
        self.assertEqual(note.read_text(), "private note")
        self.assertEqual(extra.read_text(), "user file")

    def test_uninstall_rejects_modified_code_before_removing_any_file(self):
        (self.app / "bin/priority-recalc.py").write_text("local modification")
        p = subprocess.run([sys.executable, str(ROOT / "native/uninstall.py"), "--home", str(self.home)], capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertTrue((self.app / "job-hunt").exists())

    def test_uninstall_refuses_manifest_parent_escape(self):
        import hashlib
        victim = self.home / "config.yaml"
        victim.write_text("keep global config")
        manifest = self.app / "config/install-manifest.json"
        manifest.write_text(json.dumps({"files": {"apps/job-hunt/../../config.yaml": hashlib.sha256(victim.read_bytes()).hexdigest()}}))
        p = subprocess.run([sys.executable, str(ROOT / "native/uninstall.py"), "--home", str(self.home)], capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(victim.read_text(), "keep global config")

    def test_uninstall_refuses_managed_parent_symlink_inside_home(self):
        import shutil
        code = self.app / "bin"
        victim = self.home / "shared-code"
        code.rename(victim)
        code.symlink_to(victim, target_is_directory=True)
        p = subprocess.run([sys.executable, str(ROOT / "native/uninstall.py"), "--home", str(self.home)], capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertTrue((victim / "jobpaths.py").is_file())
        self.assertTrue((self.app / "job-hunt").exists())
        code.unlink()
        shutil.move(str(victim), code)


if __name__ == "__main__":
    unittest.main()
