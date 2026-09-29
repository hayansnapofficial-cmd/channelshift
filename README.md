# ChannelShift · 채널쉬프트

고객의 자연어 요구를 **질문·답변 → 요구사양 → 화면 설계 → ERD·API → DB → 백엔드 → 프론트 → 검수 파일**로 연결하는 제작 작업실입니다. 회원별 Codex 연결과 단계별 검수 기록을 사용하며, DB 템플릿·MCP·스킬도 제공합니다.

ChannelShift is a member-scoped website production workspace, independent schema editor and stdio MCP server. It generates reviewable source bundles and verifies deterministic DDL in an isolated in-memory SQLite database. Generated applications and production deployments are not executed by the workspace.

회원 작업실: [channelshift.net](https://channelshift.net/delivery). 운영 배포 구성과 복구 방법은 [서버 구성](deploy/README.md)을 참고하세요.

## 새 제작 작업실

v0.2.0 회원 서버 실행: `channelshift-members --port 5189` 또는 저장소의 `powershell -File scripts/start-members.ps1`. [작업실](http://127.0.0.1:5189/delivery)에서 로그인하고 **내 계정 → 내 Codex 연결**을 설정합니다. 공개 HTTPS 구성은 [서버 배포 안내](docs/DEPLOYMENT.md)를 따릅니다.

1. 새 프로젝트에 고객 원문을 넣고 **내 Codex로 정리**를 누릅니다.
2. 질문에 답하고 **답변 저장하고 다시 정리**한 뒤 요구사양을 확정합니다.
3. 화면 설계 → ERD → API → SQLite → 백엔드 → 프론트를 생성하고, 결과를 확인한 이유를 남겨 다음 단계로 진행합니다. 코드 파일과 ERD는 직접 수정할 수 있습니다.
4. **운영·정책**의 사업자정보, 개인정보처리방침, 통신판매업정보, 고객문의, 호스팅사, 이용약관, 취소/환불 규정을 입력합니다. 판매·서비스·SaaS 모두 일곱 항목이 있어야 납품 파일을 만들 수 있습니다.
5. 검수 자료를 확인하고 소스·DB 생성문·정책 페이지·실행 안내를 ZIP으로 내려받습니다.

이 작업실의 첫 출력 형식은 Python 표준 라이브러리 + SQLite + HTML/CSS/JavaScript입니다. DB 생성문은 실제 메모리 DB에서 검사하지만 생성한 앱의 실행·보안 인수 검사·공개 배포는 별도로 해야 합니다. 필수 항목의 작성 완료는 법률 검토 완료를 의미하지 않습니다. 자세한 범위는 [재구축 구조](docs/STUDIO_REBUILD.md), [필수 운영 정보](docs/SITE_OBLIGATIONS.md)를 확인하세요.

기존 회원 프로젝트는 원문·답변·설계 이력을 보존해 새 작업실에서 열립니다. 변경된 상위 산출물에 의존하는 후속 검수는 다시 필요합니다. DB 단독 편집기는 `/editor`에 있습니다. [v0.2.0 배포 범위](docs/RELEASE_0_2_0.md)와 후속 운영 공정 설계를 구분합니다.

## 무엇을 만들 수 있나요?

| 기능 | 지원 범위 |
| --- | --- |
| 기본 템플릿 | 회원·권한, 콘텐츠·게시물, 예약·서비스, 상품·주문 |
| SQL | PostgreSQL, MySQL, SQLite 초기 DDL |
| Java | Java 17+ · Jakarta JPA Entity · Spring Data Repository |
| 편집기 | 테이블·필드·관계 편집, 구조 검증, SQL·Java 미리보기와 다운로드 |
| 보관 | 네이티브 JSON, 주제·프로젝트별 로컬 버전, 내용 해시 중복 방지 |
| AI 연결 | MCP 도구 11개(로컬 DB 8개·회원 공용 기능 3개), 프로젝트 적용 스킬 |

**SQL과 Java는 함께 씁니다.** SQL은 DB 구조를 정의하고 Java는 애플리케이션에서 사용하는 모델·저장소를 구현합니다. TypeScript·Python 프로젝트에도 스킬이 기존 ORM 방식에 맞춰 구조를 적용할 수 있습니다. MCP의 언어별 코드 출력은 현재 Java용입니다.

## 빠른 설치

Python 3.10 이상이 필요합니다. **Node.js·npm이나 프런트엔드 빌드가 필요 없습니다.** 설치 시 공식 패키지 저장소에서 Python 의존성을 받습니다.

Windows PowerShell:

```powershell
git clone https://github.com/hayansnapofficial-cmd/channelshift.git
cd channelshift
python -m venv .venv
.venv/Scripts/python.exe -m pip install .
.venv/Scripts/python.exe -m channelshift.launcher
```

macOS/Linux:

```bash
git clone https://github.com/hayansnapofficial-cmd/channelshift.git
cd channelshift
python -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m channelshift.web
```

작업실은 [로컬 ChannelShift](http://127.0.0.1:5187/)에서, DB 단독 편집기는 `/editor`에서 열립니다. 서버를 전경 실행했다면 Ctrl+C로 종료합니다. [릴리스](https://github.com/hayansnapofficial-cmd/channelshift/releases)에서 wheel을 받아 `python -m pip install <wheel 파일>`로 설치해도 됩니다. Windows 저장소 설치 도우미는 릴리스를 빌드한 후 `scripts/install-windows.ps1`로 실행하며 기존 가상환경·바로가기를 덮어쓰지 않습니다.

## MCP 연결

설치한 가상환경의 `channelshift-mcp`를 stdio 명령으로 등록합니다. 이 서버는 인터넷 HTTP 엔드포인트를 열지 않습니다.

Codex 예시:

```powershell
codex mcp add channelshift -- "C:\absolute\path\.venv\Scripts\channelshift-mcp.exe"
```

일반 MCP 호스트 설정 예시(실제 설치 경로로 바꾸세요):

```json
{
  "mcpServers": {
    "channelshift": {
      "command": "/absolute/path/.venv/bin/channelshift-mcp"
    }
  }
}
```

| 도구 | 역할 |
| --- | --- |
| `list_templates` | 기본 템플릿 목록 |
| `create_schema_from_template` | 템플릿·프로젝트명·DB 방언으로 네이티브 모델 생성 |
| `validate_schema` | 필드·타입·키·관계·허용 값 검증 |
| `export_sql` | 모델에서 해당 DB용 DDL 생성 |
| `export_java` | JPA Entity·Repository 파일과 적용 안내 생성 |
| `save_project` | 주제·프로젝트별 로컬 버전 저장 |
| `list_projects` | 저장한 버전 목록 |
| `get_project` | 내용 해시로 버전 읽기 |
| `service_status` | 회원 공용 기능 연결 상태 |
| `review_requirements` | 서버에서 원문·요구사항 후보의 근거 일치 검토 |
| `collect_reference` | 서버에서 공개 HTTPS 참고 페이지의 텍스트 수집 |

편집기와 MCP의 기본 저장 폴더는 `~/.channelshift`입니다. `CHANNELSHIFT_HOME`을 바꾼다면 양쪽에 같은 값을 지정하세요. MCP 저장 결과는 편집기의 **저장한 버전 → 새로고침**에서 확인합니다. 현재 편집은 자동으로 바뀌지 않습니다. 설계·키·개인 환경 설정은 배포물에 포함하지 않습니다.

회원 공용 기능은 작업실 **내 계정 → MCP 연결**에서 발급한 회원 연결 키를 사용합니다. `CHANNELSHIFT_SERVICE_URL`에는 서비스 주소를, `CHANNELSHIFT_SERVICE_TOKEN_FILE`에는 비공개 키 파일 경로를 지정합니다. 키는 30일 후 만료되며 재발급·해제로 철회할 수 있습니다. 공급자 API 키는 서버에서만 관리합니다. 자연어 처리와 제작은 MCP를 호출하는 사용자의 AI가 담당하므로 웹 프로그램의 Codex 연결 없이 공용 도구를 호출할 수 있습니다. 공개 회원 서버는 `https://channelshift.net`이며 로컬 개발 모드도 유지합니다. [회원 서버와 연결 안내](docs/MEMBER_AUTH.md).

## 스킬과 사용법

[스킬](skills/channelshift/SKILL.md)을 `~/.codex/skills/channelshift`에 설치합니다. 릴리스의 `channelshift-skill-0.2.1.zip`은 이 폴더 구조와 독립 패키지 wheel을 포함합니다. 기존 스킬을 보관한 뒤 설치하고 새 Codex 작업에서 사용하세요. ZIP 안의 `scripts/install.py`는 패키지를 별도 가상환경에 설치하며 MCP 경로를 출력합니다.

요청 예시:

> $channelshift로 이 Spring Boot 프로젝트에 맞는 예약 DB를 만들어 줘. JPA Entity와 Repository, 기존 Flyway 규칙에 맞는 마이그레이션도 작성하고 편집기에 저장해 줘.

편집기에서는 **프로젝트명·DB 선택 → 템플릿 생성 → 필드·관계 수정 → 구조 검증 → SQL/Java 생성 → 버전 저장** 순서로 사용합니다. Java 파일 선택 메뉴에서 Entity·Repository를 각각 내려받을 수 있고 전체 파일은 JSON 묶음으로도 내보냅니다. 네이티브 JSON은 다시 가져와 편집할 수 있습니다.

CLI도 제공합니다:

```text
python -m channelshift templates
python -m channelshift create booking --project MyApp --database postgresql
python -m channelshift validate --input schema.json
python -m channelshift sql --input schema.json
python -m channelshift java --input schema.json --package com.example.app
```

## 독립 구현과 범위

이 저장소의 구현은 요구사항에서 새로 작성했습니다. drawDB 코드·UI·검증기·자산과 이전 수정판은 포함하지 않습니다. [구조 설명](docs/ARCHITECTURE.md)과 [MIT 라이선스](LICENSE)를 확인하세요. SDK 등 별도 라이브러리는 [각자의 고지](THIRD_PARTY_NOTICES.md)를 유지하며, 모든 의존성이 MIT라는 뜻은 아닙니다. 정식 클린룸 인증이나 법률상 권리 문제 없음의 보증은 아닙니다.

기본 템플릿은 개발 출발점입니다. 실제 로그인·결제 처리, SQL 실행, NoSQL, 원격 동기화, 기존 drawDB 파일 자동 이관은 첫 독립판의 범위에 포함하지 않습니다. 기존 수정 앱과 그 설계·동기화 설정은 별도 보존됩니다.

Java 출력은 기존 프로젝트의 프레임워크·드라이버·명명 정책에 맞춰 적용해야 합니다. 기본 키는 앱에서 할당하고 외래키 필드는 스칼라로 유지합니다. 각 출력의 `JAVA_STARTER.md`에 적용 조건을 제공합니다. SQLite는 일부 타입 제약을 강제하지 않고 정확한 금액 계산용 DECIMAL 저장을 보장하지 않습니다. 지원하지 않는 조합은 생성 오류로 알립니다. 초기 DDL을 기존 DB에 그대로 적용하지 말고 변경 마이그레이션을 검토하세요.

## 개발 및 검증

```text
python -m pip install -e . build setuptools wheel
python -m unittest discover -s tests -v
node --check src/channelshift/web/app.js
python scripts/build-release.py
```

Node는 선택적인 JavaScript 문법 검사에만 사용합니다. 실행에는 필요 없습니다. 검사 결과와 한계는 [릴리스 기록](docs/RELEASE.md)을 따릅니다. 공개 웹사이트 배포나 `channelshift.net` 연결은 이 릴리스에 포함하지 않습니다.

## 전체 운영 공정의 설계와 남은 범위

다음 제품 목표는 **신입직원이 고객 요구사항 원문을 자연어로 접수하고, 프로그램에 연결한 본인 Codex 구독으로 홈페이지를 제작·검수·납품하는 것**입니다. 고객 원문 접수 → 프로젝트·환경 점검 → 정식 요구사항 확정을 먼저 진행합니다. 회사 소개·포트폴리오·문의·관리자 기능은 적합성을 확인할 파일럿 프로필이며 고객 요청을 템플릿에 강제로 맞추지 않습니다. 현재 실제 고객 요구사항은 접수되지 않았고 합성 예시는 검사에만 사용합니다.

[납품 공정](docs/DELIVERY_PIPELINE.md)은 요구사항 확인 뒤 **시스템 설계**와 **디자인 후보·미리보기·승인**을 진행하고, 세 계약의 정합성 검수 후 DB → 백엔드 → 프론트 → 통합검수 → 배포·인계로 이어집니다. [디자인 후보 계약](docs/DESIGN_CANDIDATES.md)은 제작 방식과 관계없이 승인된 버전을 구현 기준으로 삼습니다. [Codex 연결 설계](docs/CODEX_CONNECTION.md)는 직원 PC의 본인 계정과 이용 한도를, [회원 플랫폼](docs/MEMBER_PLATFORM.md)은 별도의 회원 신원·프로젝트 소유권·선택한 산출물 저장을 다룹니다. 새 작업실은 위에 명시한 단계별 파일 생성·검수까지 구현합니다. 고객 앱의 전체 운영 실행기, 인프라 연결, 조직별 공유는 후속 구현입니다.

공정 프로필에는 [Security Lane](docs/SECURITY_DELIVERY.md)을 추가했습니다. 원문과 내부 기준을 구분한 보안 요구사항·위협 모델·공통 접근 정책을 설계하고, **SG1 설계 검수 → 격리 DB·백엔드 정책 검사 → SG2 구현 검수 → SG3 배포 검수**를 후속 단계의 선행 조건으로 둡니다. 기존 세 계약과 시스템·디자인 병행 구조를 유지합니다. 이는 계획 의존성의 구현이며 실제 보안 검사나 배포 차단 서버가 아닙니다.

[공급자 계약](docs/PROVIDER_CONTRACTS.md)은 PostgreSQL/Supabase와 공급자 중립 AuthZ를 분리하고, SQL GRANT·RLS·관리자/우회 역할·클레임 신뢰 경계를 실제 격리 DB에서 검사하도록 정의합니다. GitHub App·웹훅·동일 SHA와 빌드의 검사 증거도 계약에 포함합니다. 연결·프로비저닝·정책 컴파일·DB 검사·웹훅 처리는 아직 미구현이며 계획 출력에 `not_connected`/`not_invoked`로 표시합니다.

[프로젝트 목록 계약](docs/PROJECT_CATALOG.md)과 [인프라·납품 계약](docs/INFRASTRUCTURE_DELIVERY.md)은 프로젝트별 작업 경계, SEO·OG·DNS/TLS, 소유권·백업·비밀값 인계·임시 접근 회수·고객 인수의 완료 기준을 다룹니다. 예시 결과나 계획을 실제 고객 프로젝트의 검사·납품 완료로 간주하지 않습니다.

v0.2.0에는 B0 기반인 공정 프로필, 읽기 전용 `delivery-plan` CLI, 입력 증거를 판정하는 순수 Gate 계산이 포함됩니다. `python -m channelshift delivery-plan` 또는 `python -m channelshift delivery-plan --input brief.json`으로 접수 상태와 계획을 조회합니다. 고객 원문이 없으면 후속 계획의 시작 조건을 충족하지 않습니다. 이 읽기 전용 CLI는 자연어 모델 호출·작업 실행·승인 발급·배포를 수행하지 않으며, 입력을 채우거나 계산이 통과해도 실제 검수·납품 완료를 뜻하지 않습니다.

개발 브랜치에는 별도로 [요구사항 접수 화면](docs/INTAKE_PILOT.md)을 구현했습니다. 로컬 서버의 `/delivery`에서 **고객 원문 등록 → 본인 Codex로 후보·질문 작성 → 선택적인 Jev 근거 검토 → 사람 개입 사유 기록**을 실행할 수 있습니다. 원문·이전 결과·개입 사유는 로컬 SQLite에 보존합니다. 실제 Codex 구독 호출과 Jev API를 합성 자료로 확인했으며, 아직 전체 홈페이지를 자동 완성하는 기능은 아닙니다.

확인 질문에는 답변을 입력·수정하고 이력을 볼 수 있습니다. 다른 탭에서 수정한 답변을 덮어쓰지 않으며, 저장한 답변은 다음 후보 재작성에 반영합니다. **저장하고 요구사항 검수**를 누르면 작성 중인 답변을 저장하고 요구사항·답변 묶음을 검수 화면으로 넘깁니다. 필수 답변 누락이나 버전 충돌은 이동을 막고, 저장된 검수 단계는 새로고침 후에도 유지됩니다. 프로젝트별 동의 화면 사용 여부와 선택한 담당자·사유도 기록합니다. 이 선택이 방문자 동의나 추적 허용을 자동으로 발급하지는 않습니다.

검수 화면의 **ERD 초안 만들기**는 저장한 원문·요구사항·답변을 본인 Codex에 전달해 테이블·필드·관계와 요구사항 연결 근거를 작성합니다. **ERD·DB 편집 열기**에서 이어서 수정하고 같은 접수 프로젝트에 저장하며 SQL·Java를 내보낼 수 있습니다. 요구사항 근거가 바뀌면 이전 초안은 재검토 대상으로 남고, 수동 편집 후에는 생성 당시 요구사항 연결을 다시 확인해야 합니다. 이 단계는 설계 초안이며 와이어프레임 승인·설계 검수 통과·실제 DB 구축을 뜻하지 않습니다.

v0.2.0의 [회원 모드](docs/MEMBER_AUTH.md)는 `python -m channelshift.member_web --port 5189`로 별도 실행합니다. 아이디·비밀번호·이메일 가입, 일회성 이메일 인증, 로그인·로그아웃과 회원별 접수·설계 저장소를 제공합니다. SMTP 연결 전에는 가입을 차단하며 브라우저 Gmail 로그인 정보를 가져오지 않습니다. 직원·고객·마케팅 업체는 일반 회원으로 가입하고, 기존 운영자의 프로젝트·Codex·Jev 자격증명을 물려받지 않습니다. 회원은 내 계정 화면에서 본인 Codex를 기기 인증으로 연결할 수 있고, 요구사항 검토·참고 자료 수집은 서버 공용 API를 사용합니다. 고정 HTTPS origin과 보안 쿠키를 지원하며 실제 서버 연결·메일·회원 인증은 배포 환경에서 검수해야 합니다. 팀 초대·비밀번호 복구는 후속 기능입니다.

로컬 운영자가 별도 초기 설정으로 생성한 마스터 계정은 일반 작업 공간과 `/admin` 회원 관리를 함께 사용합니다. 회원 목록·이메일 인증 상태·이용 정지와 복원·관리 변경 이력을 제공하고, 공개 가입이나 요청 필드로 관리자 권한을 얻을 수 없습니다. 마스터 초기 설정을 이메일 인증 완료로 표시하지 않으며, 다른 회원의 프로젝트 본문을 관리자 화면에서 열람하는 기능은 제공하지 않습니다.

모듈과 SEO·DNS 계획은 `project-catalog`, `project-plan --input project.json`, `dns-plan --input dns.json`, `seo-metadata --input seo.json` CLI로 확인합니다. SEO_BASIC의 OG·공유 미리보기 계약, 페이지 → 콘텐츠 → 사이트 기본값 우선순위, DNS 변경 전 보존 목록을 출력합니다. 실제 웹사이트 설치·DNS 변경·렌더링된 HTML 검사는 실행하지 않습니다.

편집기의 [ERD 변경 영향](docs/IMPACT_ANALYSIS.md)은 연결 JSON을 불러와 필드 수정 시 관련 API·백엔드·화면·테스트를 집계합니다. 연결되지 않은 종류와 오래된 기준은 미확인으로 표시하며, 임의의 개수나 실제 코드 전체 분석 완료를 주장하지 않습니다.

[백엔드 설계 보조](docs/BACKEND_ASSISTANT.md)는 이 공정의 DB·API·백엔드 계약을 구체화합니다. [예약 설정 예시](docs/examples/README.md)는 계약 설명용이며 첫 납품 대상이 아닙니다. [실제 업무 원장 검증](docs/LEDGER_EVIDENCE.md)은 이후 필요할 때 도입하는 별도 확장입니다.

제작 메뉴에서 **ERD·DB / API·백엔드 / 보안 검수 / SEO·공유 설정 / 납품 검수**로 이동할 수 있습니다. `/workbench`는 구현된 공정 기준 조회와 프로젝트별 SEO 초안 저장을 제공하며, 실제 백엔드 생성·API 정합성 검사·SEO 검사·배포 실행은 미구현으로 표시합니다.

MCP 공용 기능은 회원 전용 플랫폼 연결 키로 서버의 요구사항 검토·참고 자료 수집을 호출합니다. 자연어 처리 AI는 MCP 클라이언트가 사용하며, 공급자 API 키를 고객에게 배포하지 않습니다. 설정은 [회원 모드](docs/MEMBER_AUTH.md)의 MCP 연결 절을 참고하세요.
