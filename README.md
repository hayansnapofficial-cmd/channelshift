# ChannelShift · 채널쉬프트

웹사이트와 프로그램 개발을 시작하는 **DB 템플릿 생성 도구**입니다. 독립적인 데이터 모델을 중심으로 템플릿, SQL·Java 출력, MCP, 시각 편집기를 분리했습니다.

ChannelShift is an independent database starter generator, local schema editor and stdio MCP server for website and application development. It generates files; it does not execute SQL or connect to a live database.

## 무엇을 만들 수 있나요?

| 기능 | 지원 범위 |
| --- | --- |
| 기본 템플릿 | 회원·권한, 콘텐츠·게시물, 예약·서비스, 상품·주문 |
| SQL | PostgreSQL, MySQL, SQLite 초기 DDL |
| Java | Java 17+ · Jakarta JPA Entity · Spring Data Repository |
| 편집기 | 테이블·필드·관계 편집, 구조 검증, SQL·Java 미리보기와 다운로드 |
| 보관 | 네이티브 JSON, 주제·프로젝트별 로컬 버전, 내용 해시 중복 방지 |
| AI 연결 | MCP 도구 8개, 프로젝트 언어·ORM에 적용하는 Codex 스킬 |

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

편집기는 [로컬 ChannelShift](http://127.0.0.1:5187/)에서 열립니다. 서버를 전경 실행했다면 Ctrl+C로 종료합니다. [릴리스](https://github.com/hayansnapofficial-cmd/channelshift/releases)에서 wheel을 받아 `python -m pip install <wheel 파일>`로 설치해도 됩니다. Windows 저장소 설치 도우미는 릴리스를 빌드한 후 `scripts/install-windows.ps1`로 실행하며 기존 가상환경·바로가기를 덮어쓰지 않습니다.

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

편집기와 MCP의 기본 저장 폴더는 `~/.channelshift`입니다. `CHANNELSHIFT_HOME`을 바꾼다면 양쪽에 같은 값을 지정하세요. MCP 저장 결과는 편집기의 **저장한 버전 → 새로고침**에서 확인합니다. 현재 편집은 자동으로 바뀌지 않습니다. 설계·키·개인 환경 설정은 배포물에 포함하지 않습니다.

## 스킬과 사용법

[스킬](skills/channelshift/SKILL.md)을 `~/.codex/skills/channelshift`에 설치합니다. 릴리스의 `channelshift-skill-0.1.0.zip`은 이 폴더 구조와 독립 패키지 wheel을 포함합니다. 기존 스킬을 보관한 뒤 설치하고 새 Codex 작업에서 사용하세요. ZIP 안의 `scripts/install.py`는 패키지를 별도 가상환경에 설치하며 MCP 경로를 출력합니다.

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

## 다음 단계의 설계

다음 제품 목표는 **신입직원이 고객 요구사항 원문을 자연어로 접수하고, 프로그램에 연결한 본인 Codex 구독으로 홈페이지를 제작·검수·납품하는 것**입니다. 고객 원문 접수 → 프로젝트·환경 점검 → 정식 요구사항 확정을 먼저 진행합니다. 회사 소개·포트폴리오·문의·관리자 기능은 적합성을 확인할 파일럿 프로필이며 고객 요청을 템플릿에 강제로 맞추지 않습니다. 현재 실제 고객 요구사항은 접수되지 않았고 합성 예시는 검사에만 사용합니다.

[납품 공정](docs/DELIVERY_PIPELINE.md)은 요구사항 확인 뒤 **시스템 설계**와 **디자인 후보·미리보기·승인**을 진행하고, 세 계약의 정합성 검수 후 DB → 백엔드 → 프론트 → 통합검수 → 배포·인계로 이어집니다. [디자인 후보 계약](docs/DESIGN_CANDIDATES.md)은 제작 방식과 관계없이 승인된 버전을 구현 기준으로 삼습니다. [Codex 연결 설계](docs/CODEX_CONNECTION.md)는 직원 PC의 본인 계정과 이용 한도를, [회원 플랫폼](docs/MEMBER_PLATFORM.md)은 별도의 회원 신원·프로젝트 소유권·선택한 산출물 저장을 다룹니다. 전체 제작 실행기·디자인 스튜디오·강제 검수 서버·공개 회원 서비스는 후속 구현입니다.

공정 프로필에는 [Security Lane](docs/SECURITY_DELIVERY.md)을 추가했습니다. 원문과 내부 기준을 구분한 보안 요구사항·위협 모델·공통 접근 정책을 설계하고, **SG1 설계 검수 → 격리 DB·백엔드 정책 검사 → SG2 구현 검수 → SG3 배포 검수**를 후속 단계의 선행 조건으로 둡니다. 기존 세 계약과 시스템·디자인 병행 구조를 유지합니다. 이는 계획 의존성의 구현이며 실제 보안 검사나 배포 차단 서버가 아닙니다.

[공급자 계약](docs/PROVIDER_CONTRACTS.md)은 PostgreSQL/Supabase와 공급자 중립 AuthZ를 분리하고, SQL GRANT·RLS·관리자/우회 역할·클레임 신뢰 경계를 실제 격리 DB에서 검사하도록 정의합니다. GitHub App·웹훅·동일 SHA와 빌드의 검사 증거도 계약에 포함합니다. 연결·프로비저닝·정책 컴파일·DB 검사·웹훅 처리는 아직 미구현이며 계획 출력에 `not_connected`/`not_invoked`로 표시합니다.

[프로젝트 목록 계약](docs/PROJECT_CATALOG.md)과 [인프라·납품 계약](docs/INFRASTRUCTURE_DELIVERY.md)은 프로젝트별 작업 경계, SEO·OG·DNS/TLS, 소유권·백업·비밀값 인계·임시 접근 회수·고객 인수의 완료 기준을 다룹니다. 예시 결과나 계획을 실제 고객 프로젝트의 검사·납품 완료로 간주하지 않습니다.

현재 개발 변경에는 B0 기반인 공정 프로필, 읽기 전용 `delivery-plan` CLI, 입력 증거를 판정하는 순수 Gate 계산이 포함됩니다. **기존 v0.1.0 배포물의 기능이 아닙니다.** 이 코드를 포함한 소스에서 `python -m channelshift delivery-plan` 또는 `python -m channelshift delivery-plan --input brief.json`으로 접수 상태와 계획을 조회합니다. 고객 원문이 없으면 후속 계획의 시작 조건을 충족하지 않습니다. 자연어 모델 호출·작업 실행·승인 발급·배포를 수행하지 않으며, 입력을 채우거나 계산이 통과해도 실제 검수·납품 완료를 뜻하지 않습니다.

개발 브랜치에는 별도로 [요구사항 접수 화면](docs/INTAKE_PILOT.md)을 구현했습니다. 로컬 서버의 `/delivery`에서 **고객 원문 등록 → 본인 Codex로 후보·질문 작성 → 선택적인 Jev 근거 검토 → 사람 개입 사유 기록**을 실행할 수 있습니다. 원문·이전 결과·개입 사유는 로컬 SQLite에 보존합니다. 실제 Codex 구독 호출과 Jev API를 합성 자료로 확인했으며, 아직 전체 홈페이지를 자동 완성하는 기능은 아닙니다.

확인 질문에는 답변을 입력·수정하고 이력을 볼 수 있습니다. 다른 탭에서 수정한 답변을 덮어쓰지 않으며, 저장한 답변은 다음 후보 재작성에 반영합니다. **저장하고 요구사항 검수**를 누르면 작성 중인 답변을 저장하고 요구사항·답변 묶음을 검수 화면으로 넘깁니다. 필수 답변 누락이나 버전 충돌은 이동을 막고, 저장된 검수 단계는 새로고침 후에도 유지됩니다. 프로젝트별 동의 화면 사용 여부와 선택한 담당자·사유도 기록합니다. 이 선택이 방문자 동의나 추적 허용을 자동으로 발급하지는 않습니다.

개발 브랜치의 [회원 모드](docs/MEMBER_AUTH.md)는 `python -m channelshift.member_web --port 5189`로 별도 실행합니다. 아이디·비밀번호·이메일 가입, 일회성 이메일 인증, 로그인·로그아웃과 회원별 접수·설계 저장소를 제공합니다. SMTP 연결 전에는 가입을 차단하며 브라우저 Gmail 로그인 정보를 가져오지 않습니다. 직원·고객·마케팅 업체는 일반 회원으로 가입하고, 기존 운영자의 프로젝트·Codex·Jev 자격증명을 물려받지 않습니다. 회원별 Codex 연결·팀 초대·비밀번호 복구·공개 HTTPS 운영은 아직 구현하지 않았습니다.

로컬 운영자가 별도 초기 설정으로 생성한 마스터 계정은 일반 작업 공간과 `/admin` 회원 관리를 함께 사용합니다. 회원 목록·이메일 인증 상태·이용 정지와 복원·관리 변경 이력을 제공하고, 공개 가입이나 요청 필드로 관리자 권한을 얻을 수 없습니다. 마스터 초기 설정을 이메일 인증 완료로 표시하지 않으며, 다른 회원의 프로젝트 본문을 관리자 화면에서 열람하는 기능은 제공하지 않습니다.

모듈과 SEO·DNS 계획은 `project-catalog`, `project-plan --input project.json`, `dns-plan --input dns.json`, `seo-metadata --input seo.json` CLI로 확인합니다. SEO_BASIC의 OG·공유 미리보기 계약, 페이지 → 콘텐츠 → 사이트 기본값 우선순위, DNS 변경 전 보존 목록을 출력합니다. 실제 웹사이트 설치·DNS 변경·렌더링된 HTML 검사는 실행하지 않습니다.

편집기의 [ERD 변경 영향](docs/IMPACT_ANALYSIS.md)은 연결 JSON을 불러와 필드 수정 시 관련 API·백엔드·화면·테스트를 집계합니다. 연결되지 않은 종류와 오래된 기준은 미확인으로 표시하며, 임의의 개수나 실제 코드 전체 분석 완료를 주장하지 않습니다.

[백엔드 설계 보조](docs/BACKEND_ASSISTANT.md)는 이 공정의 DB·API·백엔드 계약을 구체화합니다. [예약 설정 예시](docs/examples/README.md)는 계약 설명용이며 첫 납품 대상이 아닙니다. [실제 업무 원장 검증](docs/LEDGER_EVIDENCE.md)은 이후 필요할 때 도입하는 별도 확장입니다.
