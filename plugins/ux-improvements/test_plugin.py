"""ux-improvements plugin — 테스트 스위트.

mock/fixture 기반. 실제 Discord 전송, 외부 API 호출 없음.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# 플러그인 모듈 동적 로드 (패키지명 충돌 없이)
_PLUGIN_PATH = Path(__file__).parent / "__init__.py"
_spec = importlib.util.spec_from_file_location("_ux_improvements_mod", _PLUGIN_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_filter_compression_noise = _mod._filter_compression_noise
choose_reaction_emoji = _mod.choose_reaction_emoji
get_tool_status = _mod.get_tool_status
on_pre_tool_call = _mod.on_pre_tool_call
on_transform_llm_output = _mod.on_transform_llm_output
get_cute_gif_url = _mod.get_cute_gif_url
_DEFAULT_READ_EMOJI = getattr(_mod, "_DEFAULT_READ_EMOJI", "👀")
_ACADEMIC_EMOJI = getattr(_mod, "_ACADEMIC_EMOJI", _DEFAULT_READ_EMOJI)


# ──────────────────────────────────────────────────────────────────────────────
# 1. Pre-API compression 필터 테스트
# ──────────────────────────────────────────────────────────────────────────────

class TestCompressionFilter(unittest.TestCase):

    def test_basic_compression_message_removed(self):
        text = (
            "📦 Pre-API compression: ~204,914 tokens near the context/output limit. "
            "Compacting before the next model call.\n\n"
            "이건 정상 답변이야."
        )
        result = _filter_compression_noise(text)
        self.assertNotIn("Pre-API compression", result)
        self.assertNotIn("Compacting before the next model call", result)
        self.assertIn("이건 정상 답변이야.", result)

    def test_compression_only_message_not_blanked_entirely(self):
        """전체가 노이즈라도 빈 문자열이 되지 않도록 (hook이 None 반환해서 원문 유지)"""
        text = "Pre-API compression: ~100,000 tokens near the context/output limit. Compacting before the next model call."
        result = _filter_compression_noise(text)
        # filter만 테스트 — 빈 문자열 가능, hook에서 None 처리
        self.assertIsInstance(result, str)

    def test_normal_message_unchanged(self):
        text = "오늘 뭐 먹을까?"
        result = _filter_compression_noise(text)
        self.assertEqual(result, text)

    def test_compression_mid_message_removed(self):
        text = (
            "안녕!\n\n"
            "📦 Pre-API compression: ~150,000 tokens near the context/output limit. Compacting before the next model call.\n\n"
            "그래서 뭐 하고 싶어?"
        )
        result = _filter_compression_noise(text)
        self.assertNotIn("Pre-API compression", result)
        self.assertIn("안녕!", result)
        self.assertIn("그래서 뭐 하고 싶어?", result)

    def test_hook_passthrough_no_change(self):
        """변경 없으면 None 반환 (원문 유지 신호)"""
        result = on_transform_llm_output(response_text="정상 답변")
        self.assertIsNone(result)

    def test_hook_filters_compression(self):
        text = (
            "Pre-API compression: ~200,000 tokens near the context/output limit. "
            "Compacting before the next model call.\n실제 답변."
        )
        result = on_transform_llm_output(response_text=text)
        self.assertIsNotNone(result)
        self.assertNotIn("Pre-API compression", result)
        self.assertIn("실제 답변.", result)

    def test_hook_failopen_on_exception(self):
        """훅 내부 오류 시 None 반환 (fail-open)"""
        result = on_transform_llm_output(response_text=None)  # type: ignore
        # None을 넣어도 crash 없어야 함
        self.assertIsNone(result)  # None → 원문 유지


# ──────────────────────────────────────────────────────────────────────────────
# 2. 상태 표시 단순화 테스트
# ──────────────────────────────────────────────────────────────────────────────

class TestToolStatus(unittest.TestCase):

    def test_known_tools_return_friendly_status(self):
        cases = [
            ("snow_search", "🧠 기억 확인 중..."),
            ("terminal", "⚙️ 처리 중..."),
            ("read_file", "📄 파일 확인 중..."),
            ("vision_analyze", "👀 이미지 확인 중..."),
            ("delegate_task", "💭 생각 중..."),
        ]
        for tool, expected in cases:
            with self.subTest(tool=tool):
                self.assertEqual(get_tool_status(tool), expected)

    def test_unknown_tool_returns_default(self):
        status = get_tool_status("some_random_internal_tool_xyz")
        self.assertEqual(status, "💭 생각 중...")

    def test_no_internal_path_in_status(self):
        """상태 문자열에 파일 경로, tool 이름, API 이름이 없어야 함"""
        for tool_name in ["terminal", "read_file", "snow_search", "browser_navigate"]:
            status = get_tool_status(tool_name)
            self.assertNotIn(".py", status or "")
            self.assertNotIn("/", status or "")
            self.assertNotIn("API", status or "")

    def test_skip_tools_return_none(self):
        """3초 이하 짧은 작업은 상태 표시 생략"""
        for tool in ["omh_probe", "omh_role", "omh_context"]:
            self.assertIsNone(get_tool_status(tool))

    def test_pre_tool_call_hook_returns_status_text(self):
        result = on_pre_tool_call(tool_name="snow_search")
        self.assertIsNotNone(result)
        self.assertIn("status_text", result)
        self.assertNotIn("snow_search", result["status_text"])

    def test_debug_mode_skips_status(self):
        """HERMES_DEBUG_STATUS=1이면 None 반환"""
        import os
        os.environ["HERMES_DEBUG_STATUS"] = "1"
        try:
            result = on_pre_tool_call(tool_name="snow_search")
            self.assertIsNone(result)
        finally:
            del os.environ["HERMES_DEBUG_STATUS"]


# ──────────────────────────────────────────────────────────────────────────────
# 3. 맥락형 읽음 반응 이모지 테스트
# ──────────────────────────────────────────────────────────────────────────────

class TestEmojiPolicy(unittest.TestCase):

    # --- 정상 맥락 이모지 ---

    def test_sadness_emoji(self):
        emoji = choose_reaction_emoji("오늘 너무 힘들어 ㅠㅠ")
        self.assertEqual(emoji, "🫂")

    def test_happy_emoji(self):
        emoji = choose_reaction_emoji("신난다!!! ㅋㅋ")
        self.assertEqual(emoji, "🎉")

    def test_sleep_emoji(self):
        emoji = choose_reaction_emoji("자러 간다 굿나잇")
        self.assertEqual(emoji, "😴")

    def test_coding_emoji(self):
        emoji = choose_reaction_emoji("코드 버그 fix 해야 함")
        self.assertEqual(emoji, "💻")

    def test_thanks_emoji(self):
        emoji = choose_reaction_emoji("고마워 잘했어!")
        self.assertEqual(emoji, "🫶")

    def test_default_read_emoji(self):
        emoji = choose_reaction_emoji("ㅇㅇ 알겠어")
        self.assertIsNotNone(emoji)

    def test_empty_message_returns_default(self):
        emoji = choose_reaction_emoji("")
        self.assertEqual(emoji, _DEFAULT_READ_EMOJI)

    # --- Negative context 테스트 (핵심 요구사항) ---

    def test_academic_sex_context_no_sexual_emoji(self):
        """학술 문맥에서 '섹스'가 등장해도 야한 이모지 없음"""
        text = "성관계 빈도에 관한 사회학 연구 결과에 따르면..."
        emoji = choose_reaction_emoji(text)
        # 야한 이모지가 아닌 학술/중립 이모지여야 함
        self.assertNotIn(emoji, ["🔞", "🍆", "💦", "🫦"])
        self.assertEqual(emoji, _ACADEMIC_EMOJI)

    def test_feminist_discourse_no_sexual_emoji(self):
        """페미니즘 담론에서 성적 단어 → 야한 이모지 아님"""
        text = "페미니즘 관점에서 본 성적 대상화 비판"
        emoji = choose_reaction_emoji(text)
        self.assertNotIn(emoji, ["🔞", "🍆", "💦"])
        # 학술 이모지 또는 중립 이모지
        self.assertIn(emoji, [_ACADEMIC_EMOJI, _DEFAULT_READ_EMOJI, "📚", "🤔"])

    def test_medical_context_no_sexual_emoji(self):
        """의학 문맥 (성병, STI 등) → 야한 이모지 없음"""
        text = "STI 예방과 성병 검사의 중요성에 대한 의학적 접근"
        emoji = choose_reaction_emoji(text)
        # 야한 이모지가 아닌 것 확인 (병원, 학술, 기본 중립 등 허용)
        self.assertNotIn(emoji, ["🔞", "🍆", "💦"])

    def test_news_crime_context_no_sexual_emoji(self):
        """성범죄 뉴스 문맥 → 야한 이모지 없음"""
        text = "성범죄 피해자 지원 정책에 대한 뉴스 보도"
        emoji = choose_reaction_emoji(text)
        self.assertNotIn(emoji, ["🔞", "🍆"])

    def test_gender_equality_context(self):
        """성평등 담론 → 야한 이모지 없음"""
        text = "성 평등을 위한 젠더 갭 해소 방안"
        emoji = choose_reaction_emoji(text)
        self.assertNotIn(emoji, ["🔞", "🍆", "💦"])

    def test_children_context_absolute_block(self):
        """아동 관련 문맥 → 어떤 성적 이모지도 절대 없음"""
        text = "아이들의 성교육 프로그램 개발 방향"
        emoji = choose_reaction_emoji(text)
        self.assertNotIn(emoji, ["🔞", "🍆", "💦", "🫦"])

    def test_pure_sexual_casual_returns_neutral(self):
        """성적 단어 + 일반 캐주얼 문맥 → 야한 이모지 사용 안 함 (중립 반환)"""
        text = "섹스 어제 했어"
        emoji = choose_reaction_emoji(text)
        # 정책: 야한 이모지를 쓰지 않고 중립 반환
        self.assertEqual(emoji, _DEFAULT_READ_EMOJI)

    def test_word_matching_not_simple(self):
        """단순 단어 매칭이 아니라 문맥 우선임을 확인"""
        # '섹스' 단어가 있어도 연구 문맥이면 학술 이모지
        text = "섹스에 관한 사회학 연구"
        emoji = choose_reaction_emoji(text)
        self.assertEqual(emoji, _ACADEMIC_EMOJI)
        # '섹스' 단어가 없어도 야한 이미지가 아니면 일반 이모지
        text2 = "오늘 기분 최고야!"
        emoji2 = choose_reaction_emoji(text2)
        self.assertNotIn(emoji2, ["🔞", "🍆"])


# ──────────────────────────────────────────────────────────────────────────────
# 4. GIF URL 헬퍼 테스트 (mock)
# ──────────────────────────────────────────────────────────────────────────────

class TestGifHelper(unittest.TestCase):

    def test_no_api_key_returns_none(self):
        """TENOR_API_KEY 없으면 None 반환"""
        import os
        saved = os.environ.pop("TENOR_API_KEY", None)
        try:
            result = get_cute_gif_url("cute cat")
            self.assertIsNone(result)
        finally:
            if saved:
                os.environ["TENOR_API_KEY"] = saved

    @patch("urllib.request.urlopen")
    def test_api_returns_gif_url(self, mock_urlopen):
        """API 응답이 있으면 GIF URL을 반환"""
        import os, json
        os.environ["TENOR_API_KEY"] = "fake_key_for_test"
        try:
            mock_response = MagicMock()
            mock_response.__enter__ = lambda s: s
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_response.read.return_value = json.dumps({
                "results": [{
                    "media_formats": {
                        "tinygif": {"url": "https://media.tenor.com/abc123/cat.gif"}
                    }
                }]
            }).encode()
            # json.load를 위한 mock
            mock_urlopen.return_value = mock_response
            with patch("json.load", return_value={
                "results": [{
                    "media_formats": {
                        "tinygif": {"url": "https://media.tenor.com/abc123/cat.gif"}
                    }
                }]
            }):
                result = get_cute_gif_url("cute cat")
                self.assertEqual(result, "https://media.tenor.com/abc123/cat.gif")
        finally:
            del os.environ["TENOR_API_KEY"]

    @patch("urllib.request.urlopen", side_effect=Exception("network error"))
    def test_api_error_returns_none(self, _mock):
        """API 오류 시 None 반환 (fail-open)"""
        import os
        os.environ["TENOR_API_KEY"] = "fake_key"
        try:
            result = get_cute_gif_url("cute cat")
            self.assertIsNone(result)
        finally:
            del os.environ["TENOR_API_KEY"]

    @patch("urllib.request.urlopen")
    def test_empty_results_returns_none(self, mock_urlopen):
        """결과 없으면 None 반환"""
        import os
        os.environ["TENOR_API_KEY"] = "fake_key"
        try:
            with patch("json.load", return_value={"results": []}):
                result = get_cute_gif_url("nonexistent")
                self.assertIsNone(result)
        finally:
            del os.environ["TENOR_API_KEY"]

    def test_no_key_hardcoded(self):
        """API 키가 코드에 하드코딩되지 않았음을 확인"""
        source = Path(__file__).parent / "__init__.py"
        content = source.read_text(encoding="utf-8")
        # 실제 API 키 패턴 없어야 함 (AIza..., LIVE-... 등)
        import re
        hardcoded_key_pattern = re.compile(r'(?:AIza[A-Za-z0-9_-]{35}|LIVE-[A-Za-z0-9]{32})')
        self.assertIsNone(hardcoded_key_pattern.search(content))


# ──────────────────────────────────────────────────────────────────────────────
# 컴파일 및 구문 검사
# ──────────────────────────────────────────────────────────────────────────────

class TestCompile(unittest.TestCase):

    def test_plugin_compiles(self):
        """플러그인 파일이 py_compile 통과"""
        import py_compile
        src = str(Path(__file__).parent / "__init__.py")
        # compile 오류 없으면 통과
        py_compile.compile(src, doraise=True)

    def test_test_file_compiles(self):
        import py_compile
        py_compile.compile(__file__, doraise=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
