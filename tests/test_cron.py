"""Cron changes stay paused until explicit, validated activation."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class CronTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.app = self.home / 'apps/job-hunt'
        (self.app / 'config').mkdir(parents=True)
        (self.app / 'bin').mkdir()
        (self.app / 'bin/jobmail.py').write_bytes((ROOT / 'bundle/job-search/bin/jobmail.py').read_bytes())
        (self.app / 'config/mail.yaml').write_text('backend: google-workspace\n')
        self.models = self.app / 'config/models.yaml'
        self.models.write_text('private_model: null\nprivate_provider: null\n')
        spec = importlib.util.spec_from_file_location('cron_setup', ROOT / 'native/cron-jobs.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.jobs = [{'id': str(i), 'name': 'job-hunt · ' + name, 'enabled': True}
                     for i, name in enumerate(['수집', '공고 판정', '메일과 리마인더'])]
        api = ModuleType('cron.jobs')
        api.list_jobs = Mock(return_value=self.jobs)
        api.create_job = Mock()
        def update(key, data):
            job = next(j for j in self.jobs if j['id'] == key)
            job.update(data)
            return job
        api.update_job = Mock(side_effect=update)
        monitor = ModuleType('cron.monitor')
        monitor.check_monitor = Mock()
        self.api = api
        self.modules = patch.dict(sys.modules, {'cron': ModuleType('cron'), 'cron.jobs': api, 'cron.monitor': monitor})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        previous = sys.path[:]
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), previous))

    def test_prepare_pauses_previously_active_jobs_and_preloads_selected_backend(self):
        with patch('subprocess.check_output', return_value='queue hash'):
            self.module.configure(self.app, '123')
        self.assertTrue(all(not j['enabled'] and j['state'] == 'paused' for j in self.jobs))
        self.assertEqual(self.jobs[2]['skills'], ['google-workspace', 'job-application'])

    def test_activation_without_private_model_makes_no_job_changes(self):
        with self.assertRaisesRegex(ValueError, 'private model'):
            self.module.configure(self.app, '123', activate=True)
        self.api.update_job.assert_not_called()
        self.assertFalse((self.home / 'scripts').exists())

    def test_failed_selected_backend_auth_prevents_activation_without_fallback(self):
        self.models.write_text('private_model: paid\nprivate_provider: provider\n')
        with self.assertRaisesRegex(ValueError, 'mail backend'):
            self.module.configure(self.app, '123', activate=True)
        self.api.update_job.assert_not_called()
        self.assertFalse((self.home / 'scripts').exists())


if __name__ == '__main__':
    unittest.main()
