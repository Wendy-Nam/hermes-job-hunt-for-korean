"""Resume facts stay in their own sections; PDF uses an isolated browser profile."""
import importlib.util
from contextlib import redirect_stdout
import io
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]


class DocumentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / 'bundle/job-search/bin'
        old = sys.path[:]
        sys.path.insert(0, str(path))
        try:
            spec = importlib.util.spec_from_file_location('resume_renderer', path / 'generate-tailored-resume.py')
            cls.renderer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.renderer)
        finally:
            sys.path[:] = old

    def test_dated_experience_accepts_both_heading_styles(self):
        for heading in ['Example (근무 기간: 2025.01 ~ 2025.06)', 'Example (2025.01 ~ 2025.06)']:
            with self.subTest(heading=heading):
                parsed = self.renderer.parse_master_resume('## 2. 경력\n### ' + heading + '\n- **직급/직책**: 강사\n- **주요 정량 성과**:\n  - AI 실습 12회 운영\n')
                self.assertEqual(len(parsed['experiences']), 1)
                self.assertEqual(parsed['experiences'][0]['kpis'], ['AI 실습 12회 운영'])

    def test_unrelated_sections_do_not_modify_previous_experience(self):
        for heading in ['## 4. 프로젝트', '### 별도 프로젝트', '### 교육 과정 (수료)']:
            with self.subTest(heading=heading):
                text = ('### Example (근무 기간: 2025.01 ~ 2025.06)\n'
                        '- **직급/직책**: 강사\n- **주요 정량 성과**:\n  - AI 실습 12회 운영\n'
                        + heading + '\n- **직급/직책**: 프로젝트 리더\n- **주요 정량 성과**:\n  - 별도 프로젝트 성과\n')
                parsed = self.renderer.parse_master_resume(text)
                self.assertEqual(len(parsed['experiences']), 1)
                self.assertEqual(parsed['experiences'][0]['role'], '강사')
                self.assertEqual(parsed['experiences'][0]['kpis'], ['AI 실습 12회 운영'])

    def test_pdf_uses_private_profile_and_never_accepts_stale_output(self):
        with tempfile.TemporaryDirectory() as directory:
            html = Path(directory) / 'resume.html'
            pdf = html.with_suffix('.pdf')
            html.write_text('<html>Example</html>')
            pdf.write_bytes(b'%PDF-old')
            profiles = []
            def run(command, **kwargs):
                profile = next(x.split('=', 1)[1] for x in command if x.startswith('--user-data-dir='))
                profiles.append(profile)
                self.assertFalse(Path(profile).is_relative_to(html.parent))
                self.assertIn('--disable-dev-shm-usage', command)
                target = Path(next(x.split('=', 1)[1] for x in command if x.startswith('--print-to-pdf=')))
                target.write_bytes(b'%PDF-new')
                process = MagicMock()
                process.returncode = 0
                process.pid = 987654321
                process.__enter__.return_value = process
                return process
            with patch.object(self.renderer, 'find_chrome_binary', return_value='/chrome'), patch.object(self.renderer.subprocess, 'Popen', side_effect=run), patch.object(self.renderer.os, 'killpg'):
                self.assertTrue(self.renderer.render_pdf(html, pdf))
                self.assertTrue(self.renderer.render_pdf(html, pdf))
            self.assertNotEqual(*profiles)
            self.assertTrue(all(not Path(p).exists() for p in profiles))
            self.assertEqual(pdf.read_bytes(), b'%PDF-new')
            process = MagicMock()
            process.returncode = 0
            process.__enter__.return_value = process
            with patch.object(self.renderer, 'find_chrome_binary', return_value='/chrome'), patch.object(self.renderer.subprocess, 'Popen', return_value=process), patch.object(self.renderer.os, 'killpg'):
                self.assertFalse(self.renderer.render_pdf(html, pdf))
            self.assertEqual(pdf.read_bytes(), b'%PDF-new')

    def test_pdf_failure_reports_saved_html_without_changing_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            facts = {'master_resume': '- **이름**: Example\n', 'fit_evidence': '', 'role_contexts': '', 'portfolios': ''}
            printed = io.StringIO()
            with patch.object(sys, 'argv', ['renderer', '--pdf', '--out', str(output)]), patch.object(self.renderer, 'read_source_vault', return_value=facts), patch.object(self.renderer, 'find_chrome_binary', return_value=None), redirect_stdout(printed):
                with self.assertRaisesRegex(SystemExit, 'HTML은 위 경로에 보존'):
                    self.renderer.main()
            paths = [Path(p) for p in printed.getvalue().splitlines()]
            self.assertEqual(len(paths), 2)
            self.assertTrue(all(p.is_file() and p.suffix == '.html' for p in paths))
            self.assertEqual(facts['master_resume'], '- **이름**: Example\n')

    def test_pdf_deadline_stops_browser_group_and_preserves_previous_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            html = Path(directory) / 'resume.html'
            pdf = html.with_suffix('.pdf')
            html.write_text('<html>Example</html>')
            pdf.write_bytes(b'%PDF-previous')
            process = MagicMock()
            process.pid = 987654321
            process.__enter__.return_value = process
            process.communicate.side_effect = [subprocess.TimeoutExpired('chrome', 60), ('', '')]
            with patch.object(self.renderer, 'find_chrome_binary', return_value='/chrome'), patch.object(self.renderer.subprocess, 'Popen', return_value=process), patch.object(self.renderer.os, 'killpg') as kill:
                self.assertFalse(self.renderer.render_pdf(html, pdf))
            kill.assert_called_once_with(process.pid, signal.SIGKILL)
            self.assertEqual(pdf.read_bytes(), b'%PDF-previous')

    def test_only_requested_document_is_written(self):
        for choice, suffix in [('resume', 'Resume'), ('cover-letter', 'CoverLetter'), ('portfolio', 'Portfolio')]:
            with self.subTest(choice=choice), tempfile.TemporaryDirectory() as directory:
                facts = {'master_resume': '', 'fit_evidence': '', 'role_contexts': '', 'portfolios': ''}
                with patch.object(sys, 'argv', ['renderer', '--only', choice, '--out', directory]), patch.object(self.renderer, 'read_source_vault', return_value=facts), redirect_stdout(io.StringIO()):
                    self.renderer.main()
                files = list(Path(directory).glob('*.html'))
                self.assertEqual(len(files), 1)
                self.assertTrue(files[0].name.endswith('_' + suffix + '.html'))

    def test_explicit_materials_never_fall_back_to_another_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for key in ['master_resume', 'fit_evidence', 'role_contexts', 'portfolios']:
                (folder / (key + '.md')).write_text('selected-' + key)
            selected = self.renderer.read_source_vault(folder, materials_dir=folder)
            self.assertEqual(selected['master_resume'], 'selected-master_resume')
            (folder / 'fit_evidence.md').unlink()
            with self.assertRaisesRegex(FileNotFoundError, 'fit_evidence.md'):
                self.renderer.read_source_vault(folder, materials_dir=folder)

    def test_missing_portfolio_is_omitted_and_case_evidence_is_rendered(self):
        sources = {'master_resume': '- **이름**: Example\n', 'fit_evidence': '', 'role_contexts': '', 'portfolios': ''}
        resume, _ = self.renderer.build_tailored_html('Example', 'Education', '', sources)
        self.assertNotIn('포트폴리오 URL', resume)
        sources['portfolios'] = ('### 1. 사용자 온보딩\n- **개요**: 실습 환경 구축\n'
                                 '- **성과**: 사용 시작\n- **성과 근거**: 사용자 후기 (정성)\n')
        portfolio = self.renderer.build_portfolio_html('Example', 'Education', '', sources)
        self.assertIn('사용자 후기 (정성)', portfolio)

    def test_optional_experiences_render_in_both_languages_and_respect_hidden_sections(self):
        master = ('- **이름**: Example\n'
                  '### 교육·코칭\n- 2026.01 | Learners | Workshop | Hands-on setup\n'
                  '### 오픈소스·커뮤니티\n- 2025 | Module | Maintainer | Issue response\n'
                  '### 연구 경험\n- 2024 | Lab | Assistant | Parallel tests\n'
                  '### 콘텐츠\n- 2026 | Guide | Blog | A & B tutorial\n')
        sources = {'master_resume': master, 'fit_evidence': '', 'role_contexts': '', 'portfolios': ''}
        for lang in ['ko', 'en']:
            with self.subTest(lang=lang):
                resume, _ = self.renderer.build_tailored_html('Example', 'Education', '', sources, lang=lang)
                for fact in ['Hands-on setup', 'Issue response', 'Parallel tests', 'A &amp; B tutorial']:
                    self.assertIn(fact, resume)
                self.assertNotIn('{{', resume)
                self.renderer._assert_balanced(resume, lang)
                sources['master_resume'] = '- **숨길 항목**: 오픈소스·커뮤니티\n' + master
                hidden, _ = self.renderer.build_tailored_html('Example', 'Education', '', sources, lang=lang)
                self.assertNotIn('Issue response', hidden)
                sources['master_resume'] = master

    def test_competency_brief_preserves_five_points_without_adding_episode_metrics(self):
        master = ('- **요약 방식**: 역량\n- **커리어 핵심 경쟁력**:\n'
                  + ''.join('  - 역량' + str(i) + ' — 수행 내용\n' for i in range(5))
                  + '### 에피소드 1: 사례\n- **상황**: 문제\n- **행동**: 조치\n- **정량결과**: 별도 사례 성과\n')
        sources = {'master_resume': master, 'fit_evidence': '', 'role_contexts': '', 'portfolios': ''}
        context = self.renderer.build_context('Example', 'Education', '', sources)
        self.assertEqual(len(context['summary_points']), 5)
        self.assertNotIn('별도 사례 성과', str(context['summary_points']))

    def test_responsibilities_and_qualitative_results_do_not_require_invented_metrics(self):
        text = ('### Example (2026.01 ~ 2026.02)\n- **주요 업무**:\n  - 사용자 환경 구축\n'
                '### 에피소드 1: 온보딩\n- **상황**: 설치 오류\n- **행동**: 함께 설정\n- **결과**: 직접 활용 시작\n')
        self.assertEqual(self.renderer.parse_master_resume(text)['experiences'][0]['kpis'], ['사용자 환경 구축'])
        self.assertEqual(self.renderer.parse_episodes(text)[0]['result'], '직접 활용 시작')

    def test_korean_brief_orders_background_projects_activities_and_skills(self):
        master = ('### 학력\n- 2025 | School | Degree | Graduate\n'
                  '### 자격증\n- 2026 | Certificate | Issuer\n'
                  '### 대외활동\n- 2026 | Community | Contribution\n')
        sources = {'master_resume': master, 'fit_evidence': '', 'role_contexts': '', 'portfolios': ''}
        resume, _ = self.renderer.build_tailored_html('Example', 'Education', '', sources)
        names = ['경력 사항', '>학력<', '>자격증<', '주요 프로젝트', '>대외활동<', '보유 기술</h2>']
        positions = [resume.index(name) for name in names]
        self.assertEqual(positions, sorted(positions))


if __name__ == '__main__':
    unittest.main()
