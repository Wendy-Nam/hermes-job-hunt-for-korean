# Company Research Pattern (기업 리서치)

사용자가 면접/미팅 전에 특정 회사 조사를 요청할 때 사용.

## 난이도별 접근

### 1단계 — 기본 정보 수집 (항상 시도)

```python
# 도메인 유추
domains = [
    f'https://www.{company_slug}.com',
    f'https://{company_slug}.com',
    f'https://{company_slug}.co.kr',
]
```

제목(title)만이라도 뜨면 회사 업종 파악 가능. React/Next.js 사이트면 HTML만으로 내용 추출이 어려움.

### 2단계 — 회사 사이트 탐색

JavaScript 기반 사이트(React, Next.js 등)는 단순 curl로 내용이 안 보임. 다음과 같은 우회법:

```python
# 가능한 서브페이지 시도
paths = ['/about', '/company', '/products', '/solution', '/career', '/contact', '/recruit']
for p in paths:
    req = urllib.request.Request(base + p, headers={'User-Agent': 'Mozilla/5.0'})
    resp = urllib.request.urlopen(req, timeout=8)
    html = resp.read().decode('utf-8', errors='replace')
    # JavaScript 태그 제거 후 텍스트 추출
    clean = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
    texts = re.findall(r'>([^<]{15,})<', clean)
```

React 사이트는 `<title>` 정도만 나오고 body가 텅 빔. 이 경우 다른 경로로 전환.

### 3단계 — 채용/뉴스 플랫폼

```python
# 사람인
f'https://www.saramin.co.kr/zf_user/search?searchword={회사명}'
# 잡코리아
f'https://www.jobkorea.co.kr/Search/?stext={회사명}'
```

중견/대기업은 잡히지만 중소기업은 채용 플랫폼에 안 올라오는 경우 많음.

### 4단계 — 네이버 뉴스 (k-skill-proxy)

```bash
curl -fsS --get "https://k-skill-proxy.nomadamas.org/v1/naver-news/search" \
  --data-urlencode 'q=검색어' \
  --data-urlencode 'display=5' \
  --data-urlencode 'sort=date'
```

- 검색 결과가 다른 동음이의어로 나오는 경우 주의 (예: 같은 이름의 포털 사이트와 제조사 도메인이 따로 존재)
- 뉴스에 안 나오는 회사면 소규모/비상장일 가능성 높음

### 5단계 — noembed로 유튜브 채널/영상 메타 확인

회사 공식 유튜브 채널이 있을 때:

```bash
curl -s "https://noembed.com/embed?url=https://www.youtube.com/channel/CHANNEL_ID"
```

## 면접 준비 시 정리 포맷

```
**📋 {회사명} — 리서치 결과**

| 항목 | 내용 |
|------|------|
| **회사명** | |
| **웹사이트** | |
| **업종** | |
| **위치** | |
| **직무** | |
| **규모** | 뉴스/채용공고 노출도로 추정 |
| **뉴스** | |
```