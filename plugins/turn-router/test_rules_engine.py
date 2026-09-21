"""Unit tests for turn-router's rule-tier engine (rules_engine.py, run inside the container)."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("conditional_rules", HERE / "rules_engine.py")
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MOD)


class RoutingTests(unittest.TestCase):
    def test_general_chat_loads_nothing(self):
        self.assertEqual(MOD.route_modules("원자는 어떻게 구성돼?"), [])
        self.assertIsNone(MOD.on_pre_llm_call(user_message="ㅎㅇ 뭐해", platform="discord"))

    def test_simple_action_loads_delegation_lite(self):
        self.assertEqual(
            MOD.route_modules("이 코드를 고쳐줄래"),
            ["delegation-lite", "routing-full"],
        )

    def test_complex_delegation_stacks_on_lite(self):
        # Stacking design: delegation-lite is the common contract, always
        # included; delegation-full adds complex-only gates on top.
        self.assertEqual(
            MOD.route_modules("크론 추가하고 테스트해줘"),
            ["delegation-lite", "delegation-full", "routing-full"],
        )
        self.assertEqual(
            MOD.route_modules("위임해서 단계별로 처리해"),
            ["delegation-lite", "delegation-full", "routing-full"],
        )

    def test_second_brain_stacks_with_action(self):
        # wiki-boundaries(private/sot 안전정책)가 secondbrain과 항상 stack —
        # 2026-09-09 라우팅 커버리지 감사: 파일은 있는데 아무도 안 부르던 고아 모듈이었음.
        self.assertEqual(
            MOD.route_modules("위키에 오늘 대화 정리해줘"),
            ["delegation-lite", "secondbrain", "wiki-boundaries", "routing-full"],
        )
        self.assertEqual(
            MOD.route_modules("어제 나눈 대화 기억나?"),
            ["secondbrain", "wiki-boundaries"],
        )

    def test_content_production_tier(self):
        self.assertEqual(
            MOD.route_modules("카드뉴스 만들어줘"),
            ["delegation-lite", "content-production", "routing-full"],
        )

    def test_doc_refactoring_tier_is_narrow(self):
        # 위키 문서를 명시적으로 정리/리팩토링할 때만 — 범용 "정리해"에는 안 걸림
        self.assertEqual(
            MOD.route_modules("위키 문서 정리해줘"),
            ["delegation-lite", "secondbrain", "wiki-boundaries", "doc-refactoring", "routing-full"],
        )
        self.assertEqual(MOD.route_modules("방 정리해"), ["delegation-lite", "routing-full"])

    def test_job_tier_stacks(self):
        self.assertEqual(
            MOD.route_modules("채용 공고 정리해줘"),
            ["delegation-lite", "job-rules-lite", "routing-full"],
        )

    def test_cron_and_subagent_turns_are_skipped(self):
        for platform in ("cron", "subagent"):
            self.assertIsNone(
                MOD.on_pre_llm_call(user_message="위키에 기록해줘", platform=platform)
            )

    def test_injection_is_real_content_not_import_pointer(self):
        result = MOD.on_pre_llm_call(user_message="위키에 기록 정리해줘", platform="discord")
        self.assertIsNotNone(result)
        ctx = result["context"]
        self.assertIn("<trusted_local_rules", ctx)
        self.assertNotIn("@import", ctx)
        self.assertGreater(len(ctx), 500)

    def test_outside_root_is_rejected_fail_closed(self):
        original = MOD.MODULE_PATHS["delegation-lite"]
        try:
            MOD.MODULE_PATHS["delegation-lite"] = Path("/etc/passwd")
            self.assertIsNone(
                MOD.on_pre_llm_call(user_message="이거 고쳐줘", platform="discord")
            )
        finally:
            MOD.MODULE_PATHS["delegation-lite"] = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
