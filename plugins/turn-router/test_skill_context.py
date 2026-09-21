from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent


class TurnRouterSkillContextTests(unittest.TestCase):
    def test_platform_and_profile_reach_retriever_and_content(self):
        calls = []

        class FakeRetriever:
            def retrieve_detailed(self, query, top_k=None, *, platform="", profile=""):
                calls.append(("retrieve", query, top_k, platform, profile))
                return {"skills": ["selfie-imagegen"], "layer": "L1", "skill_name": "selfie-imagegen"}

            def get_skill_content(self, name, *, platform="", profile=""):
                calls.append(("content", name, platform, profile))
                return "skill body"

        fake = types.ModuleType("skill_retriever")
        fake.get_skill_retriever = lambda: FakeRetriever()
        previous = sys.modules.get("skill_retriever")
        sys.modules["skill_retriever"] = fake
        try:
            spec = importlib.util.spec_from_file_location("turn_router_context_test", HERE / "__init__.py")
            module = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(module)
            result = module._on_pre_llm_call(
                user_message="셀카 보여줘",
                session_id="session-1",
                platform="discord",
                profile="coder",
            )
            self.assertIn("Auto-loaded Skill: selfie-imagegen", result["context"])
            self.assertEqual(
                calls,
                [
                    ("retrieve", "셀카 보여줘", 3, "discord", "coder"),
                    ("content", "selfie-imagegen", "discord", "coder"),
                ],
            )
        finally:
            if previous is None:
                sys.modules.pop("skill_retriever", None)
            else:
                sys.modules["skill_retriever"] = previous


if __name__ == "__main__":
    unittest.main(verbosity=2)
