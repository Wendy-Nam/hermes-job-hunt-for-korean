import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'integrations/aside/aside-job-apply/scripts/application_ledger.py'


class AsideLedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / 'state/applications.sqlite'
        self.policy_file = self.root / 'policy.json'
        self.policy = {'policy_id': 'batch-1', 'mode': 'auto_submit', 'authorized': True,
                       'expires_at': (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
                       'allowed_domains': ['wanted.co.kr'], 'accounts': {'wanted.co.kr': 'job-account'},
                       'approved_job_urls': [f'https://www.wanted.co.kr/wd/{i}' for i in range(1, 5)],
                       'max_submissions': 2}
        self.save_policy()

    def save_policy(self):
        self.policy_file.write_text(json.dumps(self.policy))

    def call(self, *args, ok=True):
        p = subprocess.run([sys.executable, str(SCRIPT), '--db', str(self.db), *args], capture_output=True, text=True)
        if ok:
            self.assertEqual(p.returncode, 0, p.stderr)
        return p

    def reserve(self, job=1, **kwargs):
        return self.call('reserve', '--policy', str(self.policy_file), '--url', f'https://www.wanted.co.kr/wd/{job}',
                         '--resume-id', 'education-v2', '--account', 'job-account', **kwargs)

    def test_submit_intent_blocks_duplicates_even_with_tracking_url(self):
        self.reserve()
        p = self.call('reserve', '--policy', str(self.policy_file), '--url', 'https://www.wanted.co.kr/wd/1?utm_source=email',
                      '--resume-id', 'another-resume', '--account', 'job-account', ok=False)
        self.assertNotEqual(p.returncode, 0)

    def test_intents_reserve_quota_before_clicking(self):
        self.reserve(1)
        self.reserve(2)
        self.assertNotEqual(self.reserve(3, ok=False).returncode, 0)

    def test_missing_authorization_and_expiry_stop_before_state_creation(self):
        self.policy['authorized'] = False
        self.save_policy()
        self.assertNotEqual(self.reserve(ok=False).returncode, 0)
        self.assertFalse(self.db.exists())
        self.policy['authorized'] = True
        self.policy['expires_at'] = '2000-01-01T00:00:00+00:00'
        self.save_policy()
        self.assertNotEqual(self.reserve(ok=False).returncode, 0)
        self.assertFalse(self.db.exists())

    def test_unknown_result_stays_blocked_instead_of_retrying(self):
        ticket = json.loads(self.reserve().stdout)['ticket']
        self.call('finalize', '--ticket', ticket, '--status', 'unknown', '--evidence', 'navigation timed out after final click')
        self.assertNotEqual(self.reserve(ok=False).returncode, 0)
        result = json.loads(self.call('check', '--url', 'https://www.wanted.co.kr/wd/1').stdout)
        self.assertEqual(result['status'], 'unknown')

    def test_receipt_confirms_submission_and_cannot_be_downgraded(self):
        ticket = json.loads(self.reserve().stdout)['ticket']
        self.call('finalize', '--ticket', ticket, '--status', 'submitted', '--evidence', 'visible receipt #123')
        self.assertNotEqual(self.call('finalize', '--ticket', ticket, '--status', 'unknown', '--evidence', 'late timeout', ok=False).returncode, 0)
        self.assertEqual(json.loads(self.call('check', '--url', 'https://www.wanted.co.kr/wd/1').stdout)['status'], 'submitted')

    def test_wrong_account_and_out_of_scope_job_are_rejected(self):
        self.assertNotEqual(self.reserve(99, ok=False).returncode, 0)
        p = self.call('reserve', '--policy', str(self.policy_file), '--url', 'https://www.wanted.co.kr/wd/1',
                      '--resume-id', 'education-v2', '--account', 'different-account', ok=False)
        self.assertNotEqual(p.returncode, 0)


if __name__ == '__main__':
    unittest.main()
