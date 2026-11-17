"""Canonical exports remain consistent without leaking private fields into public search."""

import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bundle/job-search/bin"))
from jobcareer import REQUIRED, parse_master, sync_career


def master():
    sections = {key: "# Original material" for key in REQUIRED}
    for language in ("ko", "en"):
        sections["master_resume." + language] = "- **이름**: Private Example\n"
        sections["portfolios." + language] = (
            "### 1. Original case\n- **개요**: original\n"
            "- **상황**: context\n- **문제**: bottleneck\n"
            "- **해결**: personal contribution\n- **임팩트**: observed change\n"
        )
    sections["search_profile"] = "keyword_blocks: [[AI education]]\n"
    sections["document_profiles"] = "schema_version: 1\nprofiles: {}\n"
    return "".join(
        f"<!-- career:{key} -->\n{value}\n<!-- /career:{key} -->\n"
        for key, value in sections.items()
    )


class CareerSyncTest(unittest.TestCase):
    def test_export_derives_episodes_and_evidence_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            app = root / "app"
            job = root / "notes"
            resume = job / "resume"
            resume.mkdir(parents=True)
            (resume / "career_master.md").write_text(master())
            first = sync_career(job, app, write=True)
            self.assertEqual(first["updated"], 15)
            self.assertEqual(sync_career(job, app, write=True)["updated"], 0)
            self.assertEqual(sync_career(job, app)["checked"], 15)
            self.assertIn(
                "personal contribution", (resume / "master_resume.md").read_text()
            )
            self.assertIn("observed change", (resume / "fit_evidence.md").read_text())
            self.assertNotIn(
                "Private Example", (app / "config/search-profile.yaml").read_text()
            )
            (resume / "career_master.md").write_text(
                master().replace("observed change", "new observed change")
            )
            with self.assertRaisesRegex(ValueError, "differ from master"):
                sync_career(job, app)
            result = sync_career(job, app, write=True)
            self.assertTrue(Path(result["backup"]).is_dir())
            (resume / "materials/ko/master_resume.md").write_text(
                "independent user edit"
            )
            with self.assertRaisesRegex(ValueError, "edited separately"):
                sync_career(job, app, write=True)
            self.assertEqual(
                (resume / "materials/ko/master_resume.md").read_text(),
                "independent user edit",
            )

    def test_missing_duplicate_and_private_search_fields_fail_before_writes(self):
        for text in [
            master().replace("<!-- /career:matching_profile -->", ""),
            master() + master(),
            master().replace(
                "keyword_blocks: [[AI education]]",
                "keyword_blocks: [[AI education]]\nresume: Private name",
            ),
        ]:
            with self.subTest(text=text[-40:]), self.assertRaises(ValueError):
                parse_master(text)

    def test_export_and_backup_symlinks_fail_before_any_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            job = root / "job"
            app = root / "app"
            resume = job / "resume"
            resume.mkdir(parents=True)
            (resume / "career_master.md").write_text(master())
            outside = root / "outside"
            outside.mkdir()
            (resume / "materials").symlink_to(outside)
            with self.assertRaises(ValueError):
                sync_career(job, app, write=True)
            self.assertFalse((app / "config/search-profile.yaml").exists())
            (resume / "materials").unlink()
            sync_career(job, app, write=True)
            (resume / "career_master.md").write_text(
                master().replace("observed change", "new result")
            )
            (resume / ".career-sync-backups").symlink_to(outside)
            with self.assertRaises(ValueError):
                sync_career(job, app, write=True)
            self.assertIn("observed change", (resume / "portfolios.md").read_text())
