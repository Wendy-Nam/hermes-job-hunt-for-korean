---
name: job-match
description: "Use to judge whether a job posting fits the user's CURRENT resume: apply as-is, strengthen the resume first, or skip (too senior / wrong domain). Junior-calibrated. Picks the right resume variant, scores fit, records it. 공고-이력서 적합도 · 지원 판단 · 이력서 갭 분석."
version: 1.2.0
author: USER
platforms: [linux, macos]  # 스크립트는 OS 중립 — 서버 설치 관례만 Linux
metadata:
  hermes:
    tags: [구직, 채용, 적합도, 매칭, 이력서, 갭분석, job]
    related_skills: [wanted-search, linkedin-search, kr-search, ats-search]
---

> **판정 기준은 이 파일에 없다 — 전부 SoT에서 읽는다 (2026-07-25 파편화 정리):**
> - **판정 프레임워크·프로필·전략·티어·핏 규칙·게이트** = `wiki/automation/job-hunting/Rules.md` — **이 스킬을 쓸 때 반드시 먼저 읽는다.**
> - **수집 필터 기계값**(연차·시니어·제외 정규식·키워드) = `search-profile.yaml`
> - **이력서 변형 표·선택 규칙** = Rules.md의 "이력서 변형" 섹션 (ko/en × sales/ops 4종, private)
> - 기준을 바꾸려면 저 파일들을 고쳐라. **이 SKILL.md에 기준 사본을 다시 쌓지 말 것** — 같은 기준이 두 곳에 살면 반드시 어긋난다(실사고 다수).

> **현재 구조**: 공고 1건 = `automation/job-hunting/postings/` 노트 1개(frontmatter: 회사·포지션·보드·티어·적합도·상태[예정/지원완료/진행중/완료]·세부·게시일·링크·추가링크1~2·메모·우선도). 수집~입고는 `job-collect.py`→`job-stage-commit.py` 자동. 수동 `--merge` 폐기. 신규 필드는 posting frontmatter에 추가한다(별도 파일·DB 금지).

# 공고-이력서 적합도 & 지원 판단 (job-match)

**핵심 질문에 답한다:** "이 공고, **지금 이력서로 지원 가능한가? / 이력서를 보완해야 하나? / 급이 안 맞나?**"
단순 발견이 아니라 **갭 분석 + 지원 의사결정**.

## 워크플로
0. **`automation/job-hunting/Rules.md`를 읽는다** — 판정 프레임워크·게이트·이번 분기 핏 규칙·티어 기준이 전부 거기 있다.
1. **JD 확보**:
   - **URL이 위키에 있는지 먼저 확인**: [[job-search-dashboard]]나 보드 파일에 공고링크가 이미 있으면 웹 검색 말고 그 URL을 쓴다.
   - `python3 SKILL_DIR/scripts/fetch_jd.py "<공고URL>"` (원티드·링크드인·ATS는 API로 깨끗 / 사람인·잡코리아는 nav 노이즈 → 사용자에게 JD 붙여넣기 요청).
   - **LinkedIn 폴백**: fetch_jd.py 실패 시 [[references/linkedin-jd-scraping.md]] 참조.
2. **시니어리티/연차 게이트** (기준·예외는 Rules와 yaml): 제목 시니어 신호 OR 본문 연차 초과 → ❌ 급부적합, 여기서 종료.
3. **변형 선택** → Rules의 이력서 변형 표 따라 ko/en × sales/ops 결정, 해당 변형 텍스트 로드.
4. **채점**: Rules의 '판정 프레임워크'(두 얼굴 게이트 → 될만한가 → 적합도 → 회사) + '이번 분기 핏 규칙' 적용.
5. **3단계 판정** (아래 출력 형식) → posting 노트 frontmatter의 `적합도`에 기록하고, Rules 티어 기준으로 `티어`도 부여한다. 기존 노트 수정은 `bin/note-set-field.py`로만, 전체 frontmatter 재작성 금지.
6. **공고 노트 요약만 믿지 말 것**: frontmatter/요약은 힌트다. 본문에서 연차·도메인·계약형태를 재확인하고, 다르면 본문 우선.
7. **반복 판정 패턴이 생기면 `Rules.md`에 축적한다** — 이 스킬의 references에 쌓지 않는다(기준 이원화 금지).
8. **우선도는 자동 파생값**: `bin/priority-recalc.py`가 유일 계산자(공식·배점은 Rules '구조 계약'이 SoT). LLM은 `티어`와 `적합도`만 판정하고 `우선도`를 손으로 쓰지 않는다.

## 출력 = 3단계 판정 ★
(판정 **기준**은 Rules — 여기는 출력 **형식**이다)
- ✅ **지원 적합 (as-is)**: 주니어/entry 롤 + 요건 대체로 충족 → **지금 이력서로 바로 지원**. (강조할 포인트 1~2개)
- 🔧 **보완 후 지원**: 방향은 맞는데 이력서에 **X가 약함/빠짐** → 무엇을(어떤 경험·키워드·수치) 채우면 되는지 구체 제시. (없는 경력을 지어내라는 게 아니라, 있는 걸 **드러내라**는 것)
- ❌ **급부적합/스킵**: 시니어급이거나 도메인 상이 → 지원 말 것. 이유 한 줄.

### 출력 예
```
[🔧 보완 후 지원] en-sales · Datadog Enterprise Sales Executive
- 갭: JD가 'SaaS 아웃바운드 3년+' 뉘앙스 → 넌 인턴 경험이라 연차 약함(경계선)
- 보완: 아웃바운드 성과를 '수치'로(미팅 N건, 파이프라인 기여) 드러내기
- 판단: 지원하되 커버레터로 저연차 임팩트 강조. Entry AE라면 ✅.
```

## Application Status Tracking (from Gmail)
"메일 읽고 지원현황 업데이트해줘" 요청 시 `references/ats-email-status-update.md`의 워크플로를 따른다:
위키 postings 먼저 확인 → 최근 2~4주 Gmail만 검색 → "thank you for applying"=접수확인(탈락 아님) → 노트와 교차확인 → `note-set-field.py`로만 갱신 → `log.md` 기록 → 애매하면 질문.

## Pitfalls (절차)
- 이력서에 없는 경력/수치 **날조 금지** (Source-of-Truth: Rules의 이력서 변형 표 + 사용자가 직접 말한 것만).
- 이력서는 private → 채점 근거는 요약만, 원문 장문 인용 금지.
- 저연차임을 잊지 말 것 — "요건 100% 충족" 기대 말고 entry에서 **성장 가능성/전이 가능 스킬**로 판단.
- **세부 상태값은 `보류`만 사용** — `미스핏`은 폐기된 값.
- **공고 URL은 위키에서 먼저 찾을 것** — "X 회사 공고 분석해줘"에 웹 검색부터 돌리지 않는다.
- **원본은 posting 노트다**: 판정·상태·세부·메모는 frontmatter에 직접 반영 + `log.md` 한 줄. 별도 지원현황 파일·DB를 만들지 않는다. 필드 갱신은 `note-set-field.py`로만.
- **회사 단위 일괄 업데이트 금지**: 같은 회사 공고 여러 개면 각각 별도 상태 — 정확한 포지션 확인 후 그 공고만 바꾼다.

## 유사공고 1-hop 확장 (similar_jobs.py)
✅/🔧 판정이 나온 원티드 공고를 시드로, 같은 직군 태그의 최신 공고를 추가 수집한다.
```bash
python3 SKILL_DIR/scripts/similar_jobs.py "<원티드 공고 URL>" --limit 10
```
시니어 제목 필터 기본 on(`--all-levels` 해제). 태그 없으면 포지션명 검색 폴백.
