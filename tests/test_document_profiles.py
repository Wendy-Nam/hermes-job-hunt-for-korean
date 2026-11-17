"""Profiles select original material without changing its wording or escaping its root."""

import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bundle/job-search/bin"))
from jobprofiles import resolve_profile, select_projects


class ProfileTest(unittest.TestCase):
    def test_legacy_and_explicit_language(self):
        self.assertEqual(resolve_profile("/unused", None), (None, "ko", {}))
        self.assertEqual(
            resolve_profile("/unused", None, "en", Path("/specific")),
            (Path("/specific"), "en", {}),
        )

    def test_profile_boundary_and_selection_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "resume").mkdir()
            path = root / "resume/document_profiles.yaml"

            def write(body):
                path.write_text(
                    "schema_version: 1\nprofiles:\n  example:\n    language: en\n"
                    + body
                )

            write(
                "    materials: materials/en\n    preserve_order: true\n    resume_projects: [Original]\n"
            )
            folder, language, profile = resolve_profile(root, "example")
            self.assertEqual(folder, root / "resume/materials/en")
            self.assertEqual(language, "en")
            self.assertTrue(profile["preserve_order"])
            for args in [
                ("missing",),
                ("example", "ko"),
                ("example", None, Path("/other")),
            ]:
                with self.assertRaises(ValueError):
                    resolve_profile(root, *args)
            for body in [
                "    materials: ../outside\n",
                "    materials: /outside\n",
                "    materials: materials\n    preserve_order: yes-no\n",
                "    materials: materials\n    resume_projects: [a, a]\n",
            ]:
                write(body)
                with self.assertRaises(ValueError):
                    resolve_profile(root, "example")
            path.write_text("schema_version: [\n")
            with self.assertRaisesRegex(ValueError, "Invalid document_profiles"):
                resolve_profile(root, "example")
            (root / "resume/link").symlink_to(root)
            write("    materials: link\n")
            with self.assertRaises(ValueError):
                resolve_profile(root, "example")
        projects = [
            {"project_name": "A", "description": "Original wording"},
            {"project_name": "B"},
        ]
        self.assertEqual(select_projects(projects, ["B", "A"]), projects[::-1])
        for pool, names in [(projects, ["missing"]), (projects + projects[:1], ["A"])]:
            with self.assertRaises(ValueError):
                select_projects(pool, names)
