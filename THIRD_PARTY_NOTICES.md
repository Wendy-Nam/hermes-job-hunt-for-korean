# Third-Party Notices

이 브랜치(`master`)는 **설치·배포용 판**이라 구성요소별 출처를 아래처럼 명시한다.
각 스킬 폴더에 LICENSE/저작권 표기가 있으면 그것이 항상 우선한다.

| 구성요소 | 출처 | 라이선스 |
|---|---|---|
| 이 브랜치의 **모든 구성요소**(`skills/*`, `bin/*`, `scripts/*`, `plugins/eagle-eye`, `wiki-template/*`, `tests/*`, `install.sh`) | 이 키트 원저작 | MIT (루트 LICENSE) |
| `skills/media/youtube-content/scripts/fetch_transcript.py` | NousResearch hermes-agent 내장 `youtube-content` 스킬 유래(PEP723 의존성·proxynet 슬롯 추가 수정) | 업스트림 hermes-agent 라이선스(MIT) |

외부 저작 플러그인(`hermes-snow-search` — LinQuan & Snow, `rtk-rewrite` — ogallotti/rtk-ai)은
**업스트림 저장소·커밋·라이선스를 확인하지 못해 배포판에서 제외했다.** 필요하면 `mirror` 브랜치에
있으니 직접 원저작자 라이선스를 확인한 뒤 쓸 것.

운영 설치본 전체를 뜬 `mirror` 브랜치는 벤더 번들·외부 유래 스킬이 섞여 있어 재배포용이 아니다.
원저작자 표기 누락을 발견하면 이슈로 알려주면 반영한다.
