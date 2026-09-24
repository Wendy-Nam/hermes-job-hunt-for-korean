---
name: web-search
description: "일반 웹(DuckDuckGo HTML)에서 잡보드 밖 회사 채용페이지 공고 검색. 키 불필요. job-collect의 5번째 보드."
author: USER
---
# Web Job Search
`python3 scripts/web_job_search.py "<키워드>" --limit 10` — 회사 자체 채용페이지·직행 등 발굴. 회사명은 도메인 추정(정확 판정은 트리아지). 연차·시니어 기준 SoT: `search-profile.yaml`+`automation/job-hunting/Rules.md`.
