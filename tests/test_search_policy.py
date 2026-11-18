"""AI track detection must not interpret retail's substring as artificial intelligence."""
from pathlib import Path
import re
import unittest
import yaml

class SearchPolicyTest(unittest.TestCase):
    def test_ai_token_boundary_in_native_profile(self):
        path=Path(__file__).resolve().parents[1]/'profiles/korean-junior-ai.yaml'
        policy=yaml.safe_load(path.read_text())
        gate=re.compile(policy['filters']['title_must_match'],re.I)
        education=re.compile(policy['track_bonus'][0][0],re.I)
        for title in ['AI 교육 담당자','AI교육 콘텐츠','AI강사','AI Training Specialist','인공지능 교육 기획']:
            self.assertTrue(gate.search(title),title)
            self.assertTrue(education.search(title),title)
        for title in ['Retail Trainer','Retail Training Manager','Training Coordinator','Email Training Specialist']:
            self.assertFalse(gate.search(title),title)
            self.assertFalse(education.search(title),title)
        senior=re.compile(policy['filters']['senior_signals'],re.I)
        for title in ['GTM Leader','AI 교육 사업 리더']:
            self.assertTrue(senior.search(title),title)
