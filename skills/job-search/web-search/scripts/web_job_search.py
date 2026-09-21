#!/usr/bin/env python3
"""일반 웹 채용공고 검색 (DuckDuckGo HTML, 키 불필요) — 보드 밖 회사 채용페이지 발굴.
Usage: python3 web_job_search.py "<키워드>" [--limit N]
출력: job-collect 표준 포맷(- **{제목}** · {회사/도메인} + URL)
"""
import json, re, sys, urllib.parse, urllib.request
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"
AS_JSON = "--json" in sys.argv
EXCL = ("saramin.co.kr", "jobkorea.co.kr", "wanted.co.kr", "linkedin.com", "indeed.com",
        "duckduckgo", "youtube.com", "blog.naver", "tistory.com")
kw = sys.argv[1] if len(sys.argv) > 1 else "채용"
lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 10
q = urllib.parse.quote(f"{kw} 채용" if "채용" not in kw else kw)
req = urllib.request.Request(f"https://html.duckduckgo.com/html/?q={q}", headers={"User-Agent": UA})
MAX_BYTES = 5 * 1024 * 1024   # 응답 상한(비정상 대용량 응답 방어)
h = urllib.request.urlopen(req, timeout=15).read(MAX_BYTES).decode("utf-8", "ignore")
seen, n, jobs = set(), 0, []
if not AS_JSON:
    print(f'# 웹검색 "{kw}" — 일반 채용페이지')
for m in re.finditer(r'result__a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', h):
    url, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
    if url.startswith("//duckduckgo.com/l/"):  # 리다이렉트 풀기
        uq = re.search(r"uddg=([^&]+)", url)
        url = urllib.parse.unquote(uq.group(1)) if uq else url
    dom = re.sub(r"^www\.", "", urllib.parse.urlparse(url).netloc)
    if not dom or any(e in dom for e in EXCL) or dom in seen:
        continue
    seen.add(dom); n += 1
    # careers./jobs. 등 채용 서브도메인 prefix를 벗기고 첫 토큰을 회사명으로 추정(정확 회사명은 트리아지가 JD로 확정).
    comp = re.sub(r"^(careers?|jobs?|recruit|apply|hr|talent|work|join|about|corp)\.", "", dom).split(".")[0]
    # ponytail: 도메인 첫 토큰=회사 추정(크로스언어·에이전시게시엔 부정확), upgrade: web 오탐이 트리아지 토큰을 먹거나 dedup이 도메인추정↔정식명 불일치로 중복 흘리면 HTML <title>/og:site_name 파싱으로 교체
    if AS_JSON:
        jobs.append({"title": title[:70], "company": comp, "url": url})
    else:
        print(f"- **{title[:70]}** · {comp} · [web]\n  {url}")
    if n >= lim: break
if AS_JSON:
    print(json.dumps(jobs, ensure_ascii=False, indent=2))
