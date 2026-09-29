# DB 공급자·공통 권한 정책·GitHub 연결 계약

상태: 2026-09-29 설계와 계획 메타데이터. PostgreSQL/Supabase 프로비저닝·접속, 권한 정책 컴파일·시뮬레이터·실제 DB 검사, GitHub App 설치·웹훅·병합은 현재 구현하지 않았다. 현재 SQL 생성기는 초기 DDL을 반환하며 이 계약의 RLS 또는 공급자 연결을 생성하지 않는다.

공통 정책은 고객과 내부 보안 기준에서 정하고, 공급자는 그 정책을 구현할 수 있는지 확인한 뒤 선택한다. 공급자 설정·SQL 문법·JWT claim 이름을 공통 정책의 정본으로 삼지 않는다. [보안 공정](SECURITY_DELIVERY.md)의 SG1·SG2·SG3와 같은 후보에 연결한다.

## Authorization Policy와 Database Provider 분리

공통 정책은 주체, 자원, 동작, 소유권·테넌트 범위, 조건, 허용·거부, 변경 가능한 속성, 출처, revision을 정의한다. 기본 결정은 거부다. `authenticated`라는 집단에 속한다는 것만으로 다른 고객의 row에 접근할 수는 없다. 관리자 역시 플랫폼 전체·특정 테넌트·특정 자원 중 어떤 범위인지 명시한다. 직원·관리자라는 표시 문자열을 인증된 권한으로 받아들이지 않는다.

| 경계 | 책임 | 정본 또는 결과 |
| --- | --- | --- |
| 인증 공급자 | 신원·세션 검증, 발급자·대상·만료·서명과 계정 상태 확인 | 검증한 주체와 claim의 출처 |
| 공통 AuthZ | 누가 어떤 자원에서 무엇을 할 수 있는지 표현 | 공급자 중립 정책 revision |
| 애플리케이션 인가 | 서비스/API·객체·필드·업무 상태별 접근과 오류 처리 | 앱 정책 및 허용·거부 검사 |
| Database Provider | DB 버전·역할·권한·RLS·트랜잭션·이관·연결 경계 | 능력 명세·설정·migration·정책 결과 |
| 검증 실행기 | 별도 격리 환경에 정책 적용 후 각 주체의 실제 동작 검사 | 환경·후보·역할에 묶인 실행 영수증 |

`SELECT/INSERT/UPDATE/DELETE`와 업무 명령을 매핑한다. 기존 row에 접근할 수 있는지와 새 값이 허용 범위를 벗어나는지 모두 정의한다. 사용자·테넌트·owner 필드 변경, 다른 테넌트 FK 연결, 삭제·복구, 뷰·함수·배치·저장소 경로가 기본 경계를 우회하지 않는지 포함한다. 임의 SQL 표현식은 공통 정책 입력으로 받지 않으며 필요한 연산은 검증한 제한된 표현식 모델로 정의하는 것이 후속 컴파일러의 요구사항이다.

공급자 능력 명세에는 지원 버전, 정책 연산, 역할·클레임 매핑, SQL GRANT와 RLS, 함수·뷰, 정책 강제 범위, 트랜잭션과 풀링, 파일 저장 정책, migration·복구, 검사 환경을 포함한다. 필요한 능력이 없거나 의미 보존을 확인하지 못하면 `HOLD`다. 지원하지 않는 조건을 삭제하거나 더 넓은 허용으로 대체하지 않는다. 공급자 선택 또는 capability digest 변경은 SG1과 관련 검사 결과를 재검토하게 한다.

## PostgreSQL과 Supabase 어댑터의 계약

PostgreSQL 공급자는 자체 운영·기존 접속·호스팅 DB를 구분하되 접속은 허가한 환경과 역할로 제한한다. Supabase 공급자는 PostgreSQL에 대한 정책과 함께 Auth·Data API·Storage·프로젝트 구성의 경계를 명시한다. 공식 공급자 후보라는 계획은 현재 연결 기능이나 동등한 능력을 보증하지 않는다. 프로젝트를 바꾸면 같은 정책이라도 해당 환경에서 다시 검사한다.

SQL GRANT는 스키마·테이블·시퀀스·함수 등 객체의 사용 권한이고, RLS는 접근할 row와 쓰기 값을 제한하는 별도 통제다. 둘 중 하나가 있다는 이유로 다른 하나를 통과시키지 않는다. API 노출 스키마와 역할의 GRANT, RLS 활성화 및 정책 적용 여부, 읽기 조건과 쓰기 조건을 각각 검사한다. 화면에서 버튼을 감추는 것은 인가 통제가 아니다.

PostgreSQL은 RLS를 켜고 적용 가능한 정책이 없으면 기본 거부를 사용한다. 정책의 `USING`과 `WITH CHECK`가 적용되는 동작을 구분하되, `WITH CHECK` 생략 시 `USING`을 재사용하는 경우가 있으므로 생략 여부만으로 취약하다고 판정하지 않는다. 실제 명령·역할별 결과를 검사한다. [PostgreSQL RLS 공식 문서](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)

DB 소유자·superuser·BYPASSRLS·서비스용 자격증명은 일반 사용자와 다른 권한 경로다. 실제 배포 역할로 테스트하며 우회 역할의 예상 동작과 그 자격증명이 접근 가능한 프로세스를 기록한다. 관리자 앱 역할의 허용 사례를 superuser로 실행해 대신하지 않는다. 브라우저에 서비스 키나 DB 우회 자격증명을 전달하지 않고, migration/관리 역할과 런타임 역할을 분리한다. RLS 강제 설정으로 해결되는 범위와 여전히 우회하는 권한을 구분한다.

PostgreSQL의 테이블 소유자는 통상 RLS를 우회하며 `FORCE ROW LEVEL SECURITY`로 소유자에게 적용할 수 있다. superuser와 BYPASSRLS 역할의 우회를 이 설정이 제거한다고 가정하지 않는다. [공식 RLS 권한 경계](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)

Supabase의 사용자 식별과 custom claim을 사용할 때는 그 claim을 누가 발급·변경할 수 있는지, 계정·멤버십 변경이 토큰과 세션에 반영되는 시점까지 명시한다. 클라이언트가 수정 가능한 메타데이터를 관리자 근거로 사용하지 않는다. Auth·Storage·서버 직접 접속과 Data API는 각자 실제 실행 경로에서 테스트한다. Advisor 결과는 추가 참고 증거이며 정책 검사를 대체하지 않는다.

Supabase에서 `raw_user_meta_data`는 사용자 수정이 가능하므로 인가 근거로 삼지 않는다. 서버 관리 `raw_app_meta_data`를 사용하더라도 JWT 갱신 전에는 변경이 즉시 반영되지 않을 수 있다. 노출 스키마의 RLS, 서비스 키의 서버 전용 취급, 뷰의 보안 실행 방식과 해당 DB 버전을 함께 확인한다. [Supabase RLS 공식 문서](https://supabase.com/docs/guides/database/postgres/row-level-security)

공급자 기본값은 연결 시 다시 확인한다. 신규 public 테이블의 API 자동 노출 변경은 기존 프로젝트의 GRANT가 안전하다는 증명이 아니며, 실제 노출 스키마와 권한을 읽어 비교해야 한다. 업그레이드 시 DB·확장·암호화 호환 조건도 능력 확인에 포함한다. 모든 버전을 지원한다고 선언하지 않는다. [API 노출 정책 변경](https://supabase.com/changelog/45329-breaking-change-tables-not-exposed-to-data-and-graphql-api-automatically), [Postgres 업그레이드 변경사항](https://supabase.com/changelog/postgres-15-19-17-11-breaking-changes)

### 공유 연결과 GUC claim의 신뢰 경계

공유 DB 역할에서 요청별 사용자·테넌트를 GUC로 전달하는 설계라면, 그 값을 설정하는 백엔드가 먼저 인증·멤버십을 검증해야 한다. 같은 DB 역할로 임의 SQL을 실행할 수 있는 상대가 claim을 설정할 수 있다면 `current_setting` 비교만으로 신뢰 경계가 생기지 않는다. DB에 직접 접속하는 클라이언트나 SQL 주입이 그 값을 바꿀 수 있는지를 별도로 다룬다.

검증한 요청만 트랜잭션 범위의 상태를 설정하도록 제한하고, 연결 풀 반환·예외·롤백·타임아웃 후 다음 사용자의 상태가 섞이지 않음을 검사한다. 값이 없거나 잘못되었을 때 거부하고 이전 요청의 claim을 재사용하지 않는다. 역할 전환·정책 또는 함수 수정 권한을 런타임에서 제거하고, SECURITY DEFINER 함수가 필요하면 호출 권한·소유자·고정 search_path·입력·우회 범위를 함께 검토한다. 이 경계를 보장할 수 없으면 자동 정책 적용을 보류한다.

## Policy Simulator와 실제 격리 DB 검증

시뮬레이터는 공통 정책이 기대하는 행렬을 설명한다. DB에서 결과가 동일한지는 별도 실행으로 확인한다. `SQL 생성됨`, `시뮬레이션 허용/거부`, `격리 DB 실행됨`, `검수됨`을 서로 다른 상태로 저장한다.

최소 검사 주체는 익명, 사용자 A/B, 테넌트 A/B, 일반 직원, 범위가 제한된 관리자, 실제 런타임 DB 역할, 소유자·우회 역할이다. 실제 고객 레코드 없이 합성 row를 생성한다. 관리자에게 항상 전체 허용을 가정하지 않고 채택한 정책의 기대 결과를 사용한다.

| 검사 | 확인할 결과 |
| --- | --- |
| SELECT | 허용 row만 반환; 거부된 row의 존재·민감 필드가 오류/집계/관계 조회로 새지 않음 |
| INSERT | owner·tenant를 다른 사람으로 지정하는 위조, 금지 필드, 교차 테넌트 참조 거부 |
| UPDATE | 기존 접근 제한과 변경 후 값의 범위 모두 적용; 소유권·역할 승격 거부 |
| DELETE | 정책상 허용 자원만 삭제; 거부 시 실제 row 보존 |
| GRANT·RLS | 객체 권한과 row 조건 각각의 부정 사례; 미정 정책이 허용으로 바뀌지 않음 |
| 관리자·우회 역할 | 앱 관리자 범위, migration과 런타임 역할 분리, bypass가 가능한 자격증명의 격리 |
| claim·세션·풀 | 위조/누락/만료/철회된 신원, 테넌트 변경, 연결 재사용·오류 후 격리 |
| 직접 경로 | API 외 DB·함수·뷰·Storage·직접 URL 접근에서 같은 경계 적용 |

실행 전 환경 ID·DB/확장 버전·migration·정책·GRANT·provider capability·테스트 모음 digest를 고정한다. 허용한 임시 DB에 설치하고 실제 인증/역할 전환 경로로 검사한다. 기대·실제 결과, 영향 row, 실패·skip·종료 코드, 환경 폐기 또는 복구 결과를 기록한다. DB 관리자만으로 실행한 green 결과, mock-only 결과, 정책 파일 존재 여부는 `database_policy_tests` 통과 증거가 될 수 없다. 검사 실패·접속 실패·필수 능력 미확인은 다음 Gate를 보류한다.

## GitHub App과 동일 SHA 검수

기본 Source Provider 후보는 필요한 저장소에만 설치하는 GitHub App이다. 사용자에게 광범위한 PAT를 받아 기본 저장하는 흐름은 만들지 않는다. 설치·조직·저장소의 불변 ID, 허가한 범위·작업, branch/ruleset 요구사항과 연결한 제품 프로젝트를 검증한다. 필요한 권한만 부여하고 installation token·webhook secret·App private key는 비밀 저장소의 버전 참조로 관리한다. 서버 토큰을 브라우저·프롬프트·Git·산출물에 넣지 않는다.

체크 이름과 녹색 표시만 신뢰하지 않는다. 다음 정보가 동일한 후보인지 검사한다.

- installation·repository·PR·head SHA·base 또는 실제 merge 후보 SHA와 artifact/build digest.
- 필수 workflow/check의 신뢰한 App 또는 발급자, 테스트·정책·의존성·환경 digest.
- 체크의 완료 여부·명시적인 success 결과·필수 검사 수; missing/pending/cancelled/timed_out/skipped/neutral은 자동 성공이 아님.
- 독립 리뷰와 승인 대상 commit·범위·유효기간, 미해결 보안 지적, 적용 ruleset 및 우회 권한.

PR head가 바뀌거나 base 변경·merge queue 재계산으로 시험한 코드와 병합 후보가 달라지면 관련 증거는 `STALE`다. 병합 직전 정본 상태를 다시 읽고 예상 head에 묶인 동시성 검사로 변경을 막는다. 배포는 검수한 동일 빌드를 사용하며 새 빌드는 새 후보다. 검증된 PR 증거와 운영 배포 권한은 별개다. 외부 기여 코드의 CI에는 운영 비밀을 제공하지 않고, 권한 있는 workflow에서 신뢰하지 않은 코드를 실행하지 않도록 워크플로 소유권과 이벤트별 권한을 검토한다.

## Webhook 수신과 정본 재확인

웹훅 수신기는 향후 인증된 서비스 경계다. 로컬 `/delivery`에 공개 수신 경로를 붙이지 않는다. 수신 순서나 JSON 본문의 상태를 곧바로 승인으로 처리하지 않는다.

1. TLS와 본문 크기 제한을 적용하고 원본 바이트에 대한 `X-Hub-Signature-256` HMAC을 상수 시간 비교로 검증한다. 파싱·큐 등록·상태 변경보다 앞선다. secret rotation도 관리한다.
2. 허용한 event/action과 payload 형태를 확인한다. installation·repository의 불변 ID가 저장한 연결 및 허가 범위와 맞아야 한다. 공격자가 보낸 URL을 그대로 조회하지 않고 검증한 API 경로를 사용한다.
3. 검증된 delivery ID와 payload digest를 트랜잭션으로 기록해 중복 배정을 막는다. 같은 ID의 다른 본문, 재전송·순서 역전·지연 사건에서 이전 상태를 복원하거나 작업을 중복 실행하지 않는다.
4. 사건은 정본 재조회 신호다. GitHub에서 현재 head·base·체크·리뷰·규칙·설치 상태를 다시 읽고 현재 후보와 대조한다. 서명은 발신 무결성을 확인하지만 결과의 최신성과 실행 권한까지 증명하지 않는다.
5. 누락·장애 후 정기 재조정으로 상태를 복구한다. 조회 실패는 미확인/HOLD다. 설치 해제·권한 회수·저장소 이전·새 SHA를 감지하면 연결과 증거를 재평가하고 허가 없는 자동 병합·배포를 하지 않는다.

필수 회귀 시나리오는 잘못된 서명, 다른 저장소/설치, 중복·순서 역전·분실 사건, 오래된 성공 체크, 같은 이름의 다른 발급자, 새 head/base, fork의 비신뢰 CI, 권한 철회, 병합 직전 SHA 변경이다. 후속 구현은 이 사례의 실제 영수증을 제시해야 한다. 현재 `delivery_profile.py`는 이러한 연결을 `not_connected`/`not_invoked`로만 표현한다.

공식 자료 확인일은 2026-09-29다. GitHub의 원본 본문 기반 HMAC 및 안전한 비교 방식을 근거로 서명 검증 계약을 정했다. delivery 중복 처리, 동일 SHA, 정본 재조회와 stale 처리는 추가 제품 요구사항이며 서명 검사 하나로 보장되지 않는다. [GitHub 웹훅 서명 검증](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)
