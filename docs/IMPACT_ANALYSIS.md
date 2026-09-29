# ERD 필드 변경의 영향 미리보기

개발 브랜치의 편집기는 필드를 수정할 때 등록된 연결 관계에서 영향받는 API·백엔드·화면·테스트를 계산한다. 결과는 변경 전 설계 기준의 미리보기이며 실제 코드를 분석해 자동으로 발견한 연결이라고 표시하지 않는다.

## 계산과 표시

`필드 → API → 백엔드 → 화면 → 테스트` 등 등록된 방향의 의존성을 따라 직접·간접 영향을 찾는다. 같은 항목에 여러 경로가 있어도 한 번만 세고, 각 항목에는 연결 경로를 제공한다. 노드 종류에 따라 별도 개수를 표시한다.

연결 정보가 없거나 기준 설계의 해시가 다르면 `UNKNOWN`이다. 검사 범위를 선언하지 않은 종류는 숫자 대신 미확인으로 표시한다. 일부 종류만 연결 범위를 선언하면 `PARTIAL`, 모든 종류를 선언한 연결망은 `LINKED`다. `LINKED`는 등록한 자료의 범위가 명시됐다는 뜻이며 실제 구현 전수 분석의 증명이 아니다.

같은 필드를 연속으로 수정하는 동안에는 수정 전 기준의 영향을 보여 준다. 다른 설계로 교체하거나 기준이 바뀌면 새 연결 자료가 필요하다. 현재 네이티브 모델은 테이블·필드의 이름을 식별자로 사용하므로 이름 변경 후 새 기준에 맞춰 연결망을 갱신해야 한다.

영향받은 항목은 재검수가 필요한 후보로 제시한다. 현재 기능은 단계 상태·승인·배포를 변경하지 않는다. 후속 공정 실행기에서 같은 변경 요청에 연결해 관련 산출물·검사·승인을 `STALE`로 표시하는 기능을 추가해야 한다.

## 연결 파일 계약

편집기의 **변경 영향**에서 JSON 파일을 불러온다. 형식은 `channelshift.traceability/v1`이다.

```json
{
  "format": "channelshift.traceability/v1",
  "schema_digest": "현재 네이티브 설계의 정규화 SHA-256",
  "covered_kinds": ["api", "backend", "screen", "test"],
  "nodes": [
    {"id": "field:inquiries:email", "kind": "field", "label": "문의 이메일"},
    {"id": "API-INQUIRY-CREATE", "kind": "api", "label": "문의 접수 API"},
    {"id": "SCREEN-CONTACT", "kind": "screen", "label": "문의 화면"}
  ],
  "edges": [
    {"from": "field:inquiries:email", "to": "API-INQUIRY-CREATE"},
    {"from": "API-INQUIRY-CREATE", "to": "SCREEN-CONTACT"}
  ]
}
```

이 코드 블록은 설명용이며 해시 자리를 실제 값으로 바꾸기 전에는 유효한 파일이 아니다. 필드 노드 ID는 `field:<테이블명>:<필드명>`이며 노드·연결의 중복과 잘못된 참조를 검사한다. 해시는 `channelshift.impact.schema_digest(schema)`로 구한다. `covered_kinds`는 작성자가 확인한 연결 범위를 표시하며 임의로 모두 넣어 완전성을 주장하지 않는다.

API는 `POST /api/impact`에 `schema`, `table_id`, `field_id`, `graph`를 전달한다. 기존 로컬 Host·Origin·프로세스 토큰 검사를 적용하며 외부 서비스를 호출하지 않는다. 개수는 코드로 계산한다. Jev를 연결 추론 보조에 사용할 수는 있지만 모델이 추측한 관계를 검토 없이 정본 그래프에 넣는 기능은 없다.

앞으로 API·백엔드·화면·테스트를 생성할 때마다 산출물과 실제 참조를 등록해야 자동으로 채워지는 영향 화면이 된다. 현재는 명시적으로 등록한 연결망을 사용하는 기반 기능이다.

화면 검증용 [합성 연결 예제](examples/booking-impact-example.json)는 [예약 스키마](examples/booking-schema.json)의 `customers.email`에 24개 예제 항목을 연결한다. 편집기에서 스키마 JSON을 가져오고 연결 JSON을 불러온 뒤 해당 필드를 수정하면 API 4·백엔드 3·화면 6·테스트 11이 나온다. 숫자를 화면에 고정한 것이 아니라 이 예제의 노드와 연결을 계산한 결과다. 실제 고객 사이트의 영향 개수로 제시하지 않는다.
