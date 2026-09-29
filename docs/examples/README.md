# 설계 예시

이 폴더는 회원 플랫폼의 백엔드 보조 계약을 설명하는 합성 예시다. 현재 MCP 또는 편집기가 이 Blueprint를 실행하는 기능은 없다. 가입·수집·운영 DB 연결·배포도 일어나지 않는다.

- `booking-schema.json`: 독립판의 기본 예약 템플릿으로 생성한 DB 설계 출발점.
- `booking-blueprint.json`: 해당 설계에 연결된 Java/Spring 백엔드 요구사항과 사용자 조정 예시.
- `../contracts/backend-blueprint.schema.json`: 이 초기 예시의 허용 항목·타입을 검사하는 JSON Schema Draft 2020-12 계약.
- `company-site-brief.json`: 고객 원문 우선 접수 CLI의 합성 입력. 실제 고객 승인이나 납품 기록이 아니다.
- `booking-impact-example.json`: `booking-schema.json`의 `customers.email`에 연결한 **합성 영향 관계 24개**다. 화면 검증용으로 API 4·백엔드 3·화면 6·테스트 11을 선언했으며 해당 구현 파일이 존재하거나 분석됐다는 뜻이 아니다. 편집기에서 스키마를 먼저 가져오고 변경 영향 패널에서 연결 파일을 불러온 뒤 해당 필드를 수정한다.

`source.schema_version_id`는 예시용 UUID이며 실제 회원 서버에 저장됐다는 증거가 아니다. `source.digest.value`는 `booking-schema.json`을 현재 Python `channelshift.store.canonical` 방식으로 직렬화한 SHA-256이다. 원본 설계 내용과 예시의 연결만 확인한다. 작성자·시각·서버 보관·거래 진실성을 증명하지 않는다.

`channelshift-local-python-json/v1`은 현재 Python의 정렬 키, 공백 없는 JSON, UTF-8, 유한 숫자 직렬화다. 다중 언어 서명 계약으로 확정한 규격이 아니며, 교차 언어 원장에는 표준 정규화 규칙과 테스트 벡터를 별도로 고정해야 한다.

예약 기본 템플릿에는 아직 작업공간 필드, 계정 인증, API, 예약 중복 방지 또는 결제 원장이 없다. Blueprint의 항목은 **추가 구현·검증할 요구사항**이다. 원본 DB만 생성해서 이 요구사항이 충족됐다고 표시하면 안 된다. `target.readiness=proposed`, `evidence.status=planned`로 그 상태를 명시한다.

사용자 조정 예시는 Java 패키지, 기본 페이지 크기, API 경로, 트랜잭션 격리 수준이다. 각 조정에 이유를 남긴다. 작업공간 권한 검사 제거처럼 플랫폼 경계를 끄는 조정은 계약에서 허용하지 않는다. 예약 경합은 격리 수준 선택만으로 해결되지 않으며 실제 잠금·제약·재시도 구현과 동시성 테스트가 필요하다.

JSON Schema 검사는 문서 형태만 확인한다. 명령 ID·경로·조정 경로 중복, API의 명령 참조, Java 예약어, ORM 호환성, 실제 소유권 검사와 업무 규칙은 별도 의미 검사·컴파일·통합 테스트 대상이다.
