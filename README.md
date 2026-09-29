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
