# LinkedIn JD Scraping via curl

> 폴백 방식: `fetch_jd.py`가 실패하거나 없을 때 LinkedIn 공고 페이지를 직접 curl로 긁는다.

## 기본 명령

```bash
curl -sL "<linkedin_job_url>" \
  -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
  -H "Accept-Language: en-US,en;q=0.9,ko;q=0.8" \
  2>/dev/null | grep -oP '"description"[^}]*'
```

## JD 추출

LinkedIn 페이지의 `<script type="application/ld+json">` 블록에 `"description"` 키로 HTML-escaped JD 본문이 들어있다.
`grep -oP`로 뽑으면 한 줄로 나오는데, HTML 태그(`<ul>`, `<li>`, `<br>`, `<strong>`)가 포함된 상태다.

## 후처리 (선택)

```bash
# HTML 태그 제거해서 읽기 쉽게
... | sed 's/<[^>]*>//g' | sed 's/&lt;/\</g; s/&gt;/\>/g; s/&amp;/\&/g; s/&quot;/\"/g; s/&#39;/\x27/g'
```

## JD 키 필드

LinkedIn 페이지의 JSON-LD에는 다음 키도 함께 있다 (같은 `grep -oP` 범위 내):

- `"employmentType"`: FULL_TIME / PART_TIME / CONTRACT
- `"hiringOrganization"`: 회사명, 로고 URL
- `"jobLocation"`: 주소, 지역

## 주의

- LinkedIn이 레이아웃을 바꾸면 동작 안 할 수 있음. 그때는 fetch_jd.py 스크립트를 먼저 시도할 것.
- `Accept-Language` 헤더에 `ko`를 포함하면 한국어 면 공고가 우선 노출됨.