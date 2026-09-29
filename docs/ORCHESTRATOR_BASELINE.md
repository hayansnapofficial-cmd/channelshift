# 홈페이지 제작 시스템의 구현 기준선

확인일: 2026-09-29. 이 문서는 현재 저장소와 계획의 경계를 기록한다.

## 실제 재사용 범위

| 현재 파일 | 확인한 책임 | 후속 구현에 필요한 것 |
| --- | --- | --- |
| `src/channelshift/core.py`, `starters.py` | 네이티브 DB 모델 검증, 초기 SQL, Java Entity·Repository 생성 | 요구사항·화면·API 계약, migration 실행, 전체 서비스 생성 |
| `src/channelshift/store.py` | 로컬 DB 설계의 내용 해시별 버전 저장 | 회원 소유권, 작업·시도·승인·증거의 영속 저장 |
| `src/channelshift/web.py`, `web/` | loopback 편집기와 Host·Origin·프로세스 토큰 검사 | 본인 Codex 연결, 제작 진행·검수 화면, 회원 인증 |
| `src/channelshift/mcp_server.py` | 명시적인 로컬 도구 8개 | 실제 제작 실행과 연결하려면 별도 검증된 런타임 필요 |
| `src/channelshift/launcher.py` | 로컬 서버·브라우저 시작 | 작업 실행기·Writer·중단·복구 관리 |

회원·권한 템플릿에 테이블이 있다는 사실은 회원 인증이 구현됐다는 뜻이 아니다. 로컬 서버 토큰이나 내용 해시는 회원 소유권·승인자의 신원을 증명하지 않는다.

첨부 설계가 가정한 PhotoShift/Foundry 실행 엔진은 이 저장소에 없다. 사용자가 지목한 별도 PhotoShift `harness-v1`의 README·스키마에서는 저장소 전용 하네스와 정적 검증 도구를 확인했다. Task/Work Result/Review 계약이 있지만 독립적인 Attempt/Provider 저장·스케줄러의 실행 구현은 확인 범위 밖이다. 직접 통합·호환됐다고 가정하거나 코드·자산을 편입하지 않았다.

## 이번 기반 구현

- `delivery_profile.py`: 표준 회사 홈페이지의 제작 순서와 누락 자료 안내. 고객 자연어 원문을 보존하지만 아직 자동 해석하지 않는다.
- `delivery_gate.py`: 제공된 정책·후보·검사·검토·승인 기록에 대한 순수 계산. 현재 후보와 연결되지 않은 기록을 통과 근거로 사용하지 않는다.
- `python -m channelshift delivery-plan`: 입력 JSON을 검사하고 계획을 출력한다. Codex 로그인, 파일 생성, DB 실행, 승인 발급은 수행하지 않는다.
- `codex_intake.py`, `jev_review.py`: 별도의 명시적 요청으로 구독 Codex의 자연어 후보 작성과 Jev 근거 판단을 수행한다. 임시 작업공간에서 읽기 전용으로 호출하고 원문 인용을 검증한다.
- `delivery_workspace.py`, `/delivery`: 로컬 SQLite의 원문·작업 사건·과거 후보·사람 개입 이유와 접수 화면. 인증된 회원 서비스나 전체 제작 스케줄러가 아니다.

Gate의 호출자는 보호된 저장소에서 정본 정책과 인증된 증거를 가져와야 한다. 이 모듈에는 저장소나 신원 검증이 없으므로 임의 JSON을 입력한 `PASS`는 실행 허가가 아니다. HTTP/MCP 실행 권한 판정으로 직접 노출하지 않는다.

예제 확인:

```text
python -m channelshift delivery-plan --input docs/examples/company-site-brief.json
```

예제는 합성 자료이며 미결정 항목을 비워 두었다. 계획 결과의 `runtime_connected: false`, `natural_language_processing: not_invoked`는 이 읽기 전용 명령이 실행기를 연결하거나 모델을 호출하지 않았다는 뜻이다. 별도 접수 UI의 실제 호출과 구분한다. 다음 단계나 모델 호출을 흉내 내서 완료로 표시하지 않는다.

## 파일럿 이후 연결할 수직 기능

1. 현재 로컬 Codex 상태·인증 방식·지원 CLI 확인과 원문 후보 작성을 바탕으로 프로그램 내부 로그인·계정 분리를 연결한다. 전체 연결과 사용 한도는 [Codex 연결 계약](CODEX_CONNECTION.md)을 따른다.
2. 현재 후보·누락 질문에 담당자의 답변·범위 확정·정정 SRC 추가와 ChangeRequest를 연결한다.
3. 프로젝트 하나에서 작업·시도·불변 산출물·이벤트를 저장한다. 허용 범위 안의 실행·실패·재시도와 중단 확인을 연결한다.
4. 회사 소개·문의 폼·관리자 문의 목록을 첫 기능 단위로 검증한다. 앞단 기획 검수 이후에만 DB·API 구현을 연결한다.
5. 검사·독립 검토·사람 결정의 출처를 인증하고 서버에서 단계 규칙을 집행한 후 공정을 확장한다.

완료 기준은 화면에 단계가 나열되는 것이 아니라, 재시작 후 작업 복원, 누락·오래된 증거의 진행 차단, 문의 저장과 관리자 조회의 실제 동작이다. 전체 구현 단계와 납품 조건은 [납품 공정](DELIVERY_PIPELINE.md)을 따른다.

## 입력 자료

사용자 제공 `website_delivery_orchestrator_design_v1.md`의 SHA-256은 `2ee59a986505d23d3ea669687dca04c545d174a7c400c7815006166f7705234d`다. 설계 참고 자료의 식별자이며 출처 권리·구현 성공의 증명은 아니다. 개인 경로나 인증 정보는 이 문서에 포함하지 않는다.
