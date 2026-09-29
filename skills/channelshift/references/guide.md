# 독립판 사용 안내

MCP는 템플릿·모델·검증·코드 생성을 제공하고 스킬은 프로젝트 요구와 기존 개발 관례에 맞게 결과를 적용한다. 시각 편집기는 같은 네이티브 모델을 다루는 별도 화면이다.

기본 템플릿: `membership`, `content`, `booking`, `commerce`.
SQL 방언: `postgresql`, `mysql`, `sqlite`.
Java 출력: Java 17 이상, Jakarta Persistence, Spring Data JPA. 프로젝트에 맞는 의존성과 DB 드라이버는 별도로 필요하다.

1. 프로젝트명·방언·템플릿을 고르고 생성한다.
2. 테이블·필드·관계를 편집한다.
3. 검증 결과의 문제를 고친다.
4. SQL·Java를 미리 보고 내려받거나 네이티브 JSON을 내보낸다.
5. 저장하면 주제·프로젝트별 로컬 폴더에 내용 해시의 새 버전이 생긴다.

기본 저장 위치는 `~/.channelshift`이다. `CHANNELSHIFT_HOME`으로 변경할 수 있다. 편집기와 MCP에는 동일한 값을 지정한다. 설치 위치와 설계 저장 위치는 별도이며, 스킬을 제거해도 설계를 자동 삭제하지 않는다.

SQLite의 동적 타입 등 DB별 표현력 차이가 있다. 코드 생성은 실제 DB 적용이나 인증·결제 기능 구현이 아니다. 기존 DB에서는 초기 DDL을 바로 실행하지 말고 변경 마이그레이션으로 검토한다.

기존 drawDB 기반 수정판은 별도 앱이며 이 독립판에서 자동 가져오거나 원격 동기화하지 않는다. 기존 설계를 옮기는 기능은 첫 버전에 포함하지 않는다. MIT인 독립판과 기존 AGPL 앱의 출처·의무를 혼동하지 않는다.

## 공용 기능 연결

MCP에는 로컬 DB 도구 8개와 서버 공용 도구 3개가 있습니다. 공용 도구는 웹 프로그램의 MCP 연결에서 발급한 회원 키를 파일로 저장하고, MCP 프로세스의 `CHANNELSHIFT_SERVICE_URL`과 `CHANNELSHIFT_SERVICE_TOKEN_FILE`에 주소와 절대 파일 경로를 지정해야 사용할 수 있습니다. POSIX 파일 권한은 0600으로 제한합니다. 공급자 키나 Codex 인증 파일을 설정에 넣지 않습니다. 공개 서버 주소는 `https://channelshift.net`입니다. MCP의 `CHANNELSHIFT_SERVICE_URL`을 이 주소로 지정합니다. 로컬 개발 서버를 별도로 실행한 경우에만 localhost 주소를 사용합니다.
