# OO테크놀로지 — 면접 대비 기업 연구 예시 (가상 사례)

> 이 파일은 `company-interview-research` 스킬의 적용 예시입니다.
> 연구일시: D-1 | 면접: 익일 오후, OO시 OO구 (가상 주소)

---

## 조사 방법

| 소스 | 사용한 방법 | 획득한 정보 |
|------|------------|------------|
| 회사 웹사이트 (SPA) | JS 번들 다운로드 후 한국어/구조적 데이터 추출 | 회사소개, 4대 사업부문, 고객사, 파트너사, 주소 |
| Composio Search Web | 영문/한글 검색 | 대표자명(홍길동·가상), 신용평가 보고서 존재 확인 |
| Naver News | k-skill-proxy 경유 검색 | 뉴스 없음 (false-positive만 확인됨) |
| Session Memory | snow_search | 면접 일정/주소 확인 |

## 핵심 발견 사항

1. **SPA 사이트 분석 기법**: example-company.com은 React SPA (Vite + TypeScript). curl로는 빈 HTML만 받아지므로 JS 번들(`assets/index-hlYWX6GD.js`)을 직접 다운로드하여 `ko:{name:"...",tag:"...",desc:"..."}` 패턴으로 4개 사업부문, 고객사 리스트, 파트너사, 주소를 추출.

2. **4대 사업부문**:
   - LitePoint (무선 테스트) — LitePoint 한국 유일 공식 서비스 제공업체
   - Quantifi Photonics (실리콘 포토닉스 테스트)
   - MLTP (초고속 인터커넥트 테스트)
   - Teradyne PBT (인써킷/보드 테스트) — Teradyne 한국 공식 파트너

3. **고객사**: 삼성전자, LG전자, SKT, KT, LGU+, Qualcomm, MediaTek, Naver, Kakao 등

4. **False-positive 경험**: "OO테크놀로지" Naver 검색 결과 10건 모두 타사 기사. "OO테크"이 "유사 음절의 타사명"의 부분 문자열로 매칭됨.

5. **Tech stack**: React + TypeScript + Tailwind CSS, Replit 호스팅, Google Frontend

## 면접 키포인트

- LitePoint 한국 독점 공식 파트너라는 입지가 가장 강력한 세일즈 포인트
- 기술영업은 RF/Wireless 테스트 장비 판매 + 기술지원 + 교육 + A/S
- 경쟁사 인지 및 차별화 포인트 준비