# Wiki Structure & Templates

Concrete directory layout and page templates for a personal-knowledge second-brain wiki.

## Directory Layout

```
wiki/
├── profile/          # 나에 대한 것(프로필·이력)
├── journal/<YYYY-MM>/   # 일일 노트: YYYY-MM-DD.md
├── areas/            # 지속 관리 영역: health.md · finance.md 등 (폴더 아닌 페이지)
├── projects/         # 진행 중인 일·태스크 노트 (interview/ life/ 하위)
├── concepts/         # 지식·개념
├── people/           # 인물
├── automation/job-hunting/             # 구직: Rules.md(판정 기준) · postings/(공고 1건=노트 1개) · resume/
├── hermes/           # 에이전트 운영 문서(runbooks.md · decisions.md)
├── inbox/            # 미처리 캡처
├── raw/              # 불변 원본(articles/ transcripts/ clippings/)
├── _archive/         # 폐기·대체분 (tasks/ notes/)
├── index.md · SCHEMA.md · log.md
```

## 페이지 만드는 규칙

`templates/` 폴더는 없다(옛 구조 잔재였다). 새 노트는 아래 규칙대로 해당 폴더에 바로 만든다.

| 무엇 | 어디에 | 비고 |
|---|---|---|
| 일일 노트 | `journal/<YYYY-MM>/YYYY-MM-DD.md` | 월 버킷 |
| 지속 관리 영역(건강·재정·관계 등) | `profile/<키워드>.md` | **폴더 만들지 말고 페이지에 append** |
| 진행 중인 일·태스크 | `projects/` | frontmatter: `title`·`상태: open`·`priority`·`due`·`tags: [task]` |
| 지식·개념 | `concepts/` | |
| 인물 | `people/` | |
| 구직 공고 | `automation/job-hunting/postings/` | 공고 1건 = 노트 1개. **생성은 `job-stage-commit.py`만** |
| 에이전트 운영 | `hermes/runbooks.md` · `hermes/decisions.md` | append |
| 원본·클리핑 | `raw/` | 불변 |

## Frontmatter Convention

```yaml
---
title: "Page Title"
created: 2026-07-05
updated: 2026-07-05
tags: [SCHEMA.md의 분류 체계에서]
private: true    # 개인정보가 있으면 true — 파일 권한도 0600으로 만들어진다
---
```

## Hash-Based Change Detection

`/opt/data/.wiki-hashes.json` stores per-file SHA256 hashes. The daily-wiki-log cron
compares current hashes against stored state to detect actual content changes,
not filesystem metadata noise (mtime changes from Syncthing touch operations).

- [NEW] — file exists now but wasn't in previous hash state
- [MOD] — hash differs from stored hash (content changed)
- [DEL] — file was in previous hash state but no longer exists