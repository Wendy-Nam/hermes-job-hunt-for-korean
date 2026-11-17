import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch, Mock
from contextlib import nullcontext
import tempfile
import types
import sys

ROOT = Path(__file__).resolve().parents[1]


class PublicReviewTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("public_review", ROOT / "native/public-review.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_only_public_job_fields_reach_model(self):
        records = [{"url": "https://example.com/jobs/1", "title": "AI Trainer", "company": "Example",
                    "jd_text": "공개 교육 직무", "resume": "PRIVATE RESUME", "email": "PRIVATE EMAIL",
                    "profile": {"salary": "PRIVATE SALARY"}, "메모": "PRIVATE NOTE"}]
        data = self.module.request_payload(records, "pub-role-free-validated")
        wire = json.dumps(data, ensure_ascii=False)
        self.assertNotIn("PRIVATE", wire)
        self.assertIn("공개 교육 직무", wire)
        self.assertEqual(data["model"], "pub-role-free-validated")

    def test_free_failure_has_no_paid_retry(self):
        response = Mock()
        response.raise_for_status.side_effect = RuntimeError("free pool unavailable")
        with patch.object(self.module.requests, "post", return_value=response) as request:
            with self.assertRaises(RuntimeError):
                self.module.call_free([], "http://localhost/v1", "pub-role-free-validated", "test-key")
        self.assertEqual(request.call_count, 1)

    def test_malformed_free_output_retries_once_on_the_same_free_route(self):
        bad = Mock()
        bad.json.return_value = {"choices": [{"message": {"content": "not json"}}]}
        good = Mock()
        good.json.return_value = {"choices": [{"message": {"content": '[{"url":"https://example.com/1","category":"ai_education"}]'}}]}
        with patch.object(self.module.requests, "post", side_effect=[bad, good]) as request:
            rows = self.module.call_free([{"url": "https://example.com/1"}], "http://localhost/v1", "pub-role-free-validated", "test-key")
        self.assertEqual(len(rows), 1)
        self.assertEqual(request.call_count, 2)
        self.assertTrue(all(c.kwargs["json"]["model"] == "pub-role-free-validated" for c in request.call_args_list))

    def test_model_cannot_invent_urls_or_add_private_fields(self):
        good = json.dumps([{"url": "https://example.com/jobs/1", "category": "ai_education",
                            "summary": "공개 요약", "resume": "untrusted extra"}])
        rows = self.module.validate_response(good, {"https://example.com/jobs/1"})
        self.assertNotIn("resume", rows[0])
        with self.assertRaises(ValueError):
            self.module.validate_response(good, {"https://example.com/jobs/2"})

    def test_all_new_public_records_are_reviewed_in_bounded_batches(self):
        import yaml
        with tempfile.TemporaryDirectory() as td:
            app = Path(td)
            (app / "config").mkdir()
            (app / "config/models.yaml").write_text(yaml.safe_dump({"endpoint": "http://localhost/v1", "public_model": "free"}))
            rows = [{"url": f"https://example.com/{i}", "title": "AI 교육", "company": "Example"} for i in range(13)]
            (app / "public-candidates.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
            (app / ".job-staging.json").write_text(json.dumps(rows))
            paths = types.ModuleType("jobpaths")
            paths.STATE_DIR = app
            noteio = types.ModuleType("noteio")
            noteio.note_lock = nullcontext
            noteio.write_atomic = lambda p, text: Path(p).write_text(text)
            noteio.DRY_RUN = False
            calls = []
            def fake(records, *_):
                calls.append(len(records))
                return [{"url": r["url"], "category": "ai_education", "summary": "공개 요약"} for r in records]
            with patch.object(self.module, "__file__", str(app / "public-review.py")), patch.object(self.module, "call_free", side_effect=fake), patch.dict(sys.modules, {"jobpaths": paths, "noteio": noteio}), patch.dict("os.environ", {"OMNIROUTE_API_KEY": "test"}):
                self.module.main()
                self.assertEqual(len(json.loads((app / "public-reviews.json").read_text())), 13)
                self.assertTrue(all(n <= 6 for n in calls))
                before = len(calls)
                self.module.main()
                self.assertEqual(len(calls), before)
                (app / "public-candidates.jsonl").write_text("")
                (app / "public-reviews.json").write_text("{}")
                self.module.main()
                self.assertEqual(len(json.loads((app / "public-reviews.json").read_text())), 13)


if __name__ == "__main__":
    unittest.main()
