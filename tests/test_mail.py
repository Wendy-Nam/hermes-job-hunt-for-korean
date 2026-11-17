import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MailBackendTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('jobmail', ROOT / 'bundle/job-search/bin/jobmail.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.app = self.home / 'apps/job-hunt'
        (self.app / 'config').mkdir(parents=True)

    def select(self, backend):
        (self.app / 'config/mail.yaml').write_text('backend: ' + backend + '\n')

    def test_no_backend_has_no_mail_access(self):
        self.assertEqual(self.module.mail_skills(self.app), ['job-application'])
        self.assertEqual(self.module.check_backend(self.app, self.home)['mode'], 'notes-only')

    def test_google_workspace_preloads_actual_skill_and_checks_auth_without_exposing_account(self):
        self.select('google-workspace')
        self.assertEqual(self.module.mail_skills(self.app), ['google-workspace', 'job-application'])
        p = self.home / 'skills/productivity/google-workspace/scripts/setup.py'
        p.parent.mkdir(parents=True)
        p.write_text('print("AUTHENTICATED: private-account@example.test")\n')
        status = self.module.check_backend(self.app, self.home)
        self.assertTrue(status['ready'])
        self.assertNotIn('private-account', json.dumps(status))

    def test_failed_auth_does_not_fall_back_to_composio(self):
        self.select('google-workspace')
        self.assertFalse(self.module.check_backend(self.app, self.home)['ready'])
        self.assertEqual(self.module.mail_skills(self.app), ['google-workspace', 'job-application'])

    def test_composio_is_explicit_and_uses_shared_application_workflow(self):
        self.select('composio')
        self.assertEqual(self.module.mail_skills(self.app), ['job-application'])
        self.assertEqual(self.module.check_backend(self.app, self.home)['backend'], 'composio')

    def test_invalid_backend_is_rejected(self):
        self.select('other-account')
        with self.assertRaises(ValueError):
            self.module.mail_skills(self.app)


if __name__ == '__main__':
    unittest.main()
