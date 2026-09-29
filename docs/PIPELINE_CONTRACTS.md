# 제작 산출물 연결 계약

검사 프로필은 `channelshift.pipeline-contract/v2`다. 아래 검사는 파일 구조와 연결 선언을 확인한다. 생성한 Python·JavaScript·테스트를 실행하거나 요구 기능의 작동을 증명하지 않는다. `runtime_behavior_verified`, `application_execution_performed`, `test_execution_performed`는 false다.

## 화면 설계와 ERD

`wireframe/screens.json`의 화면은 고유한 SCREEN ID, 경로, 요구사항 ID를 선언한다. 모든 확정 REQ가 적어도 한 화면에서 참조되어야 한다. 참조 누락 검사이며 기능 구현 증명이 아니다. 승인된 화면 설계 파일과 해시를 ERD 입력 및 생성 이력에 고정한다. ERD 수정 이유는 1~1,000자로 저장한다. 실제 변경이 없는 저장에는 이유를 요구하지 않는다.

## API

`api/openapi.json`의 각 operation에는 고유한 `operationId`와 아래 세 확장이 필요하다.

```json
{
  "x-channelshift-requirement-ids": ["REQ-001"],
  "x-channelshift-fields": ["inquiries.id", "inquiries.message"],
  "x-channelshift-screens": ["SCREEN-001"]
}
```

요구사항·필드·화면이 승인된 입력에 존재하는지 검사한다. DB나 화면을 사용하지 않는 API는 해당 배열을 비워 둔다. 요구사항 배열은 비울 수 없다.

요청·응답·파라미터의 JSON 스키마는 제한된 하위 집합으로 검사한다. 기본 자료형, 객체 properties/required/additionalProperties, 배열 items, enum/const, 지역 참조, allOf/anyOf/oneOf/not, 길이·개수·숫자 범위 및 일부 설명 속성을 지원한다. 재귀 참조, JSON 이외 본문 형식, 알 수 없는 키는 거절한다. 전체 OpenAPI 규격 검증이나 데이터의 실제 응답 검증이 아니다. 검사 노드·깊이·파일 크기에 한도가 있다.

## 백엔드

`backend/app.py`에 최상위 함수와 정적 경로 표를 선언한다.

```python
ROUTES = {("GET", "/api/inquiries"): list_inquiries}
```

`backend/routes.json`에는 모든 API를 정확히 한 번 연결한다.

```json
{"routes":[{"operation_id":"listInquiries","handler":"list_inquiries","test_file":"backend/test_app.py","test_symbol":"InquiryTests.test_list"}]}
```

API의 메서드·경로와 표가 일치해야 한다. 함수가 존재하고 단순 pass/미구현 본문이 아닌지, 지정 테스트에 assertion이 있는지 AST로 확인한다. 함수 실행, 서버 기동, 테스트 통과, 실제 라우팅 사용 여부는 검증하지 않는다. 원본 파일을 import하지 않는다.

## 프론트

`frontend/screens.json`에 승인된 모든 화면과 HTML 파일·API를 연결한다.

```json
{"screens":[{"screen_id":"SCREEN-001","file":"frontend/index.html","operation_ids":["listInquiries"]}]}
```

API의 화면 선언과 프론트의 API 선언이 정확히 일치해야 한다. HTML 지역 링크와 지원하는 리터럴 fetch 경로·메서드를 검사한다. 동적 URL·옵션·메서드·복합 표현식은 미검증으로 집계한다. JavaScript 전체 파서나 실행 검사가 아니다.

## 영향 분석과 재검수

현재 산출물의 검사 결과로 필드 → API → 백엔드 함수·테스트 및 API → 화면 그래프를 만든다. 항목별 중복을 제거해 개수를 계산한다. 연결이 없는 필드와 미완성 범위는 0이 아니라 미확인이다. 검사 버전이나 승인된 입력이 바뀌면 기존 파일을 보존하고 하위 승인을 무효화한다. 기존 생성 파일은 상위 승인 후 이유를 입력해 재검사할 수 있다. 새 검사를 통과한 뒤에도 작업자의 승인이 필요하다.
