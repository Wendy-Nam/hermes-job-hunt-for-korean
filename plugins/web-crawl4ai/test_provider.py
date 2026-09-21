"""Unit tests for web-crawl4ai (no browser needed — subprocess is faked)."""
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, "/opt/data/plugins/web-crawl4ai")
from __init__ import Crawl4AIWebExtractProvider as P  # noqa: E402


class T(unittest.TestCase):
    def test_name(self):
        self.assertEqual(P().name, "crawl4ai")

    def test_search_unsupported(self):
        self.assertFalse(P().supports_search())
        self.assertTrue(P().supports_extract())

    def test_bad_url_shape(self):
        rows = P().extract(["not-a-url"], timeout=5)
        self.assertEqual(len(rows), 1)
        self.assertIn("error", rows[0])
        self.assertEqual(rows[0]["raw_content"], "")

    def test_missing_python_unavailable(self):
        with patch("__init__.CRAWL_PY") as m:
            m.is_file.return_value = False
            self.assertFalse(P().is_available())

    def test_timeout_shape(self):
        import subprocess as sp
        with patch("__init__.subprocess.run", side_effect=sp.TimeoutExpired("x", 1)):
            rows = P().extract(["https://example.com"], timeout=1)
            self.assertIn("timeout", rows[0]["error"])

    def test_truncation(self):
        import subprocess as sp
        import json as J
        payload = J.dumps({"success": True, "title": "t", "content": "x" * 5000, "error": ""})

        class R:
            returncode = 0
            stdout = payload + "\n"
            stderr = ""

        with patch("__init__.subprocess.run", return_value=R()):
            rows = P().extract(["https://example.com"], max_chars=100, timeout=50)
            self.assertEqual(len(rows[0]["content"]), 100)


if __name__ == "__main__":
    unittest.main()
