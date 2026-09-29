# 사이트 기본 정보와 정책

이 모듈은 판매형(`sales`), 서비스형(`service`), SaaS형(`saas`) 사이트에 **사업자정보, 개인정보처리방침, 통신판매업정보, 고객문의, 호스팅사, 서비스이용약관, 취소환불규정** 일곱 항목을 항상 포함하는 제품 기준을 구현한다. 모든 유형에서 동일하게 작성해야 하며, 유형 변경이나 면제 플래그로 항목을 생략할 수 없다.

`ready: true`는 정해진 필드가 작성되었다는 뜻이다. 사업자·신고 정보의 진위, 약관의 효력, 개인정보 처리의 적법성, 개별 거래의 환불 기준을 검증한 결과가 아니다. `legal_review_required`는 작성 후에도 항상 `true`다. 일곱 항목이 모든 사이트에서 법적으로 동일하게 의무라는 판단을 하지 않으며, 실제 서비스와 거래에 맞는 검토를 별도로 진행해야 한다.

## 입력 계약

`site_obligations.catalog()`는 폼 메타데이터와 `empty_values`를 반환한다. 빈 값에는 예시 상호, 사업자 번호, 환불 기간이나 요율을 넣지 않는다. `validate(values)`는 아래 **전체 구조와 문자열 값만** 허용하고, 공백 정리와 검증을 거친 별도 사본을 반환한다. 미작성 문자열은 임시 저장할 수 있다.

```json
{
  "company": {"name": "", "representative": "", "business_number": "", "address": ""},
  "commerce": {"registration_number": ""},
  "contact": {"email": "", "phone": ""},
  "hosting": {"name": ""},
  "policies": {"privacy": "", "terms": "", "refund": ""}
}
```

| 항목 ID | 필드 | 문자 제한 |
| --- | --- | --- |
| `business_info` | `company.name`, `company.representative`, `company.business_number`, `company.address` | 160, 80, 20, 500 |
| `privacy_policy` | `policies.privacy` | 12,000 |
| `commerce_info` | `commerce.registration_number` | 160 |
| `customer_contact` | `contact.email`, `contact.phone` | 254, 40 |
| `hosting_provider` | `hosting.name` | 160 |
| `terms_of_service` | `policies.terms` | 12,000 |
| `refund_policy` | `policies.refund` | 12,000 |

정리한 문자열 값의 UTF-8 합계는 120,000바이트 이하이다. 제어 문자, 방향 전환 문자, 일부 보이지 않는 채움 문자는 거부하며, 공백만 있는 정책은 미작성으로 남는다. 정책의 줄바꿈은 보존한다. 비어 있지 않은 사업자등록번호는 숫자 10자리 또는 `000-00-00000` 형식, 이메일은 일반적인 ASCII 주소 형식, 전화번호는 국제번호 `+`와 숫자·공백·괄호·하이픈 형식을 지원한다. 전화번호의 숫자는 7~15자리여야 한다. 이 검사는 실재하는 연락처나 등록번호임을 확인하지 않는다. 한글 이메일 주소는 현재 지원하지 않는다.

`assess(values)`는 다음을 반환한다.

- `ready`: 일곱 항목 모두 작성되었는지 여부
- `missing_items`: 미작성 항목 ID 목록
- `missing_fields`: 미작성 필드의 점 경로 목록
- `complete_count`, `required_count`: 작성 항목 수와 필수 항목 수(항상 7)
- `legal_review_required`: 항상 `true`

알 수 없는 필드, 잘못된 형식·자료형, 제한 초과는 `ValueError('invalid_site_obligations')`로 처리한다. 오류에는 입력 원문을 포함하지 않는다. 내용의 사실성이나 충분성을 자동으로 판정하거나, 상호·번호·정책을 자동으로 채우지 않는다.

## 로컬 HTML 생성

`render_pages(values, site_name)`은 완료된 값에 한해 `{path, content}` 목록을 반환한다. 사이트 이름은 200자 이하의 비어 있지 않은 문자열이어야 한다. 미작성 항목이 있으면 `ValueError('site_obligations_incomplete')`로 처리한다.

- `footer.html`: 일곱 항목을 모두 표시하는 공통 푸터 조각
- `privacy.html`, `terms.html`, `refund.html`: 입력한 정책 본문과 공통 푸터
- `contact.html`: 이메일·전화번호와 공통 푸터

모든 사용자 문자열을 HTML 이스케이프한다. 정책의 HTML·Markdown은 실행하거나 해석하지 않는다. 링크는 고정된 `/privacy.html`, `/terms.html`, `/refund.html`, `/contact.html`, `/` 및 검증·인코딩한 `mailto:`/`tel:` 링크만 사용한다. 스크립트, 외부 리소스, 자동 수집, 문의 전송 기능은 없다. 경로는 내보내기용 상대 경로이고 링크는 사이트 루트 기준이므로, 하위 경로에 배치할 때는 내보내기 통합부에서 경로 정책을 정해야 한다.

이 순수 함수는 파일을 저장하거나 사이트를 배포하지 않는다. 호출하는 생성 파이프라인이 반환된 푸터를 사이트의 실제 페이지에 포함하고, 정책 파일을 함께 내보내야 한다. 반환값만 얻은 상태를 사이트에 게시된 것으로 취급하지 않는다.

## 공식 자료와 검토 범위

확인일: **2026-09-29**. 출처 제목·URL·시행일·확인일은 `catalog().sources`에도 보존한다. 아래는 입력 필드의 참고 근거이며, 일곱 항목 전체의 법적 적용 여부를 자동 결정하는 규칙이 아니다.

- 전자상거래 사이버몰의 사업자 신원과 약관 표시를 참고했다. 상호·대표자·주소·연락처·사업자등록번호를 별도 필드로 둔다. [전자상거래법 제10조, 2026-07-21 시행](https://www.law.go.kr/LSW/lsLinkCommonInfo.do?lsJoLnkSeq=1022342373)
- 통신판매 신고 정보와 거래조건 안내를 참고했다. 취소·청약철회, 반품·환급, 고객 불만 처리 등의 실제 조건은 사업자와 검토자가 작성해야 한다. 정기결제 등 거래 형태별 내용도 확인해야 하며, 공통 환불 기간이나 공제율을 자동 삽입하지 않는다. [전자상거래법 제13조, 2026-07-21 시행](https://law.go.kr/LSW/lsLawLinkInfo.do?chrClsCd=010202&lsJoLnkSeq=1013449713)
- 사이버몰의 호스팅서비스 제공자 상호 표시를 참고해 호스팅사를 별도 항목으로 유지한다. [전자상거래법 시행령 제11조의4, 2026-07-21 시행](https://law.go.kr/LSW/lsLinkCommonInfo.do?lspttninfSeq=63473)
- 개인정보 처리방침에는 실제 처리 목적, 보유기간, 파기, 권리 행사, 담당자 또는 담당 부서와 연락처 등을 서비스의 처리 현황에 맞게 기재해야 한다. 제3자 제공·위탁 등은 해당 여부를 확인한다. 본문 필드가 채워져 있어도 이런 내용이 충분하다는 판정은 하지 않는다. [개인정보 보호법 제30조, 2026-09-11 시행](https://www.law.go.kr/lsLinkCommonInfo.do?lsJoLnkSeq=1029331583)

통신판매 신고 정보는 현재 신고번호 문자열만 구조화한다. 신고기관 등 추가 정보가 필요한 실제 표시는 이 기본 필드만으로 충분하다고 가정하지 말고 확장·검토해야 한다. 개인정보 담당자, 효력 발생일 등 정책별 세부 사항은 작성된 본문에서 검토하며 임의 기본값을 만들지 않는다. 법령 변경 시 출처와 모듈의 참고 기준을 함께 다시 확인한다.
