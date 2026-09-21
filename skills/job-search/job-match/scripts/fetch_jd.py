#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""공고 URL → JD 본문 텍스트 추출 (board-aware, 매칭용).

원티드/링크드인은 API(JS렌더라 정적 HTML엔 본문 없음), 사람인/잡코리아·일반은 HTML.
Usage: python3 fetch_jd.py "<job_url>"
"""
import html as _html, ipaddress, json, re, socket, sys, urllib.parse, urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
MAX_BYTES = 5 * 1024 * 1024   # JD 본문에 5MB면 충분 — 그 이상은 끊는다(과대 다운로드 방지)


def _check(url):
    """공개 http(s) 주소인지 검사. 여기 오는 URL은 공고·메일 등 외부 입력이라
    내부 주소(메타데이터 엔드포인트·사설망·localhost)로 유도될 수 있다(SSRF).

    한계(정직하게): 검사 시점과 실제 연결 시점에 이름을 각각 해석하므로 그 사이에 DNS 응답이
    바뀌면(재바인딩) 우회될 수 있다. 완전한 차단은 해석된 IP로 직접 연결(+Host/SNI 수동 설정)해야
    하는데, 그러면 TLS 검증이 복잡해진다. 여기서는 '기본 방어'로 둔다 —
    실수·단순 유도는 막고, 표적 재바인딩 공격까지 막지는 못한다. 이 스크립트는 공개 채용
    사이트만 상대하므로 위험 대비 복잡도가 맞지 않는다고 판단했다."""
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ValueError(f"차단: 허용되지 않는 스킴 {p.scheme!r}")
    if not p.hostname:
        raise ValueError("차단: 호스트 없음")
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except socket.gaierror as e:
        raise ValueError(f"차단: DNS 실패 {e}")
    for *_, sa in infos:                       # DNS가 여러 IP를 주면 전부 검사
        ip = ipaddress.ip_address(sa[0])
        if not ip.is_global or ip.is_multicast:   # 사설·루프백·링크로컬(169.254.169.254 등) 전부 차단
            raise ValueError(f"차단: 비공개 주소 {ip} ({p.hostname})")
    return url


class _Redirect(urllib.request.HTTPRedirectHandler):
    """리다이렉트 대상도 매번 재검사 — 공개 도메인이 내부 주소로 넘기는 우회를 막는다."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_Redirect)


def get(url):
    _check(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with _opener.open(req, timeout=20) as r:
        return r.read(MAX_BYTES).decode("utf-8", "ignore")   # 상한까지만


def strip_html(h):
    h = re.sub(r"(?is)<(script|style|noscript|svg|head).*?</\1>", " ", h)
    h = re.sub(r"(?i)<br\s*/?>", "\n", h)
    h = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", h)
    h = _html.unescape(re.sub(r"<[^>]+>", " ", h))
    return "\n".join(l for l in (re.sub(r"[ \t]+", " ", x).strip() for x in h.splitlines()) if len(l) > 1)


def wanted(job_id):
    d = json.loads(get(f"https://www.wanted.co.kr/api/v4/jobs/{job_id}"))
    job = d.get("job") or d.get("data", {}).get("job") or d
    comp = (job.get("company") or {}).get("name", "")
    detail = job.get("detail") or {}
    parts = [f"[회사] {comp}", f"[포지션] {job.get('position','')}"]
    for k in ("intro", "main_tasks", "requirements", "preferred_points", "benefits"):
        if detail.get(k):
            parts.append(f"[{k}]\n{detail[k]}")
    return "\n\n".join(p for p in parts if p.strip())


def linkedin(job_id):
    h = get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}")
    m = re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', h, re.S)
    body = strip_html(m.group(1)) if m else strip_html(h)
    title = re.search(r'topcard__title[^>]*>\s*(.*?)\s*<', h)
    comp = re.search(r'topcard__org-name-link[^>]*>\s*(.*?)\s*<', h) or re.search(r'topcard__flavor[^>]*>\s*(.*?)\s*<', h)
    head = " · ".join(_html.unescape(x.group(1).strip()) for x in (title, comp) if x)
    return (head + "\n\n" + body) if head else body


def saramin(rec_idx):
    """사람인은 상세본문이 JS iframe이라 view 페이지엔 없음 → 서버렌더 view-detail 엔드포인트 직접."""
    h = get(f"https://www.saramin.co.kr/zf_user/jobs/relay/view-detail?rec_idx={rec_idx}")
    text = strip_html(h)
    imgs = re.findall(r'https?://pds\.saramin\.co\.kr/[^"\' >]+\.(?:jpg|jpeg|png|gif)', h)
    imgs = list(dict.fromkeys(imgs))
    if imgs:
        text += "\n\n[상세이미지(vision용)] " + " ".join(imgs[:4])
    return text


def main():
    if len(sys.argv) < 2:
        print("usage: fetch_jd.py <job_url>", file=sys.stderr); return 2
    url = sys.argv[1]
    try:
        if "wanted.co.kr" in url and (m := re.search(r"/wd/(\d+)", url)):
            text = wanted(m.group(1))
        elif "linkedin.com" in url and (m := re.search(r"(?:/jobs/view/[^/?]*?-|/jobPosting/)(\d+)", url)):
            text = linkedin(m.group(1))
        elif "saramin.co.kr" in url and (m := re.search(r"rec_idx=(\d+)", url)):
            text = saramin(m.group(1))
        else:
            text = strip_html(get(url))   # 사람인/잡코리아/일반 (서버렌더)
    except Exception as e:
        print(f"에러: JD 조회 실패 — {e}. 로그인/JS 필요 시 사용자에게 JD 붙여넣기 요청.", file=sys.stderr)
        return 1
    if len(text) > 6000:
        text = text[:6000] + "\n...[truncated]"
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
