# 프로젝트 구성과 모듈 계약

상태: 2026-09-29 V1 설계와 순수 계획 생성기. 실제 서비스 연결·전체 어드민·방문자 태그 실행은 구현하지 않았다. 모듈 명세를 선택하면 요구사항 후보와 작업 명세를 만들며, 설치 완료로 표시하지 않는다.

## 현재 제공 범위

`project_catalog.py`의 공개 함수는 `catalog()`, `presets()`, `build_project_plan(config)`, `build_dns_change_plan(config)`, `resolve_seo_metadata(config)`다. 파일·환경변수·자격증명을 읽거나 네트워크·공급자 API·코드 실행을 하지 않는다. 입력은 최대 128 KiB의 제한된 JSON 계약이다.

```powershell
python -m channelshift project-catalog
python -m channelshift project-plan --input project-plan.json
python -m channelshift seo-metadata --input seo-metadata.json
```

각 결과에는 버전과 내용 해시가 붙는다. 이 해시는 같은 내용을 식별할 뿐 작성자 인증·고객 승인·실행 권한을 증명하지 않는다. HTTP/MCP에 설치나 승인 권한을 추가하지 않는다.

## V1의 8개 업무 모듈

아래는 데이터 모델과 어댑터의 경계를 고정하는 계약이다. 인터페이스 이름은 구현 계획이며 모두 현재 동작하는 API를 뜻하지 않는다. 모든 자원은 향후 인증된 `workspace_id`, `project_id`, 불변 `revision`, 내용 digest, 행위자·시각·근거를 전달한다.

| 업무 모듈 | 주 데이터 | 입력 → 출력 인터페이스 | 완료 조건 |
| --- | --- | --- | --- |
| Project | Project, Membership, ModuleSelection, IntegrationConnection, SecretRef, AssetOwnership | `configureProject(sourceRefs, selections)` → ProjectConfigRevision | 원문 존재, 소유권·환경·적용 정책 결정 |
| Requirement | SourceRecord, RequirementCandidate, ClientQuestion, Decision, ChangeRequest | `compileRequirements(sourceRevision, configRevision)` → BusinessContractCandidate | 고객 확인 및 미해결 필수 질문 처리 |
| Architecture | DomainModel, SchemaRevision, AccessPolicy, ApiContract, BackendSpec | `proposeSystem(businessRevision)` → SystemContractCandidate | ERD·DB·RLS·API·권한 계약의 독립 검수 |
| Design | DesignCandidate, PreviewBuild, DesignDecision, OgImageCandidate | `prepareDesign(businessRevision, apiRevision)` → DesignContractCandidate | 동일 후보의 미리보기와 사람 승인 |
| Build | Task, Attempt, ArtifactManifest, BuildResult, TestEvidence | `buildApprovedContracts(business, system, design)` → ReleaseCandidate | 격리 실행·컴파일·DB·기능·통합 테스트 증거 |
| Security | ThreatModel, ScanEvidence, Finding, ExceptionDecision | `assessRelease(candidateDigest, policyDigest)` → SecurityDecision | SAST/SCA/비밀/DAST/RLS/Security E2E의 적용 검사 |
| Infrastructure | Environment, ProviderBinding, ZoneSnapshot, ChangePlan, Deployment, Verification | `planInfrastructure(releaseDigest, targetRevision)` → 승인 대기 계획 | 대상·diff·snapshot·권한·검증·복구 계획 결합 |
| Delivery | AssetHandover, Acceptance, Runbook, BackupEvidence, AccessRevocation | `prepareHandover(releaseDigest, domainEvidence)` → DeliveryCandidate | 실제 도메인 확인·고객 인수·소유권·임시 권한 회수 |

작업자는 후보를 제안하고 프로그램은 버전을 추적한다. 자동검사와 독립 리뷰, 해당 역할의 사람 결정은 서로 다른 기록이다. 승인된 세 계약의 정합성 검수 이후에만 실제 Build 권한을 줄 수 있다. 현재 순수 계획기는 그러한 실행 권한을 발급하지 않는다.

## Integration Catalog와 Admin Module Catalog

Integration은 개발 기반, SEO, 분석, 광고, 로그인, 지도, 상담·메일·SMS, 결제를 구분한다. 카탈로그에 이름이 있다는 사실은 라이브 어댑터가 구현되었다는 뜻이 아니다. 현재 모든 항목은 `specification_only`, `adapter_status=not_implemented`이며 공급자 연결 작업은 수동 계획으로 남는다. 결제·예약처럼 표준 회사 사이트 범위를 넓히는 선택은 별도 고객 범위 확인과 프로필 검수가 필요하다.

각 향후 Module Spec은 다음 자료의 **실제 버전과 검증 상태**를 갖춰야 한다.

- 요구사항 템플릿, ERD fragment, migration, API 계약, backend·admin·frontend 구현물
- 인증·인가·RLS·업로드·외부 전송 규칙, 테스트 묶음, 설치 안내, 납품 체크리스트
- 공식 문서 URL·확인일·어댑터 버전·지원 작업·필요 scope·공개 설정 필드·SecretRef
- DNS 필요 여부, 환경별 설정, 외부 전송 항목, Consent 분류, 비용·제한, 고객 계정 소유권

현재 생성기는 위 자료의 작업 ID와 요구 검사만 만든다. SQL migration·API·어드민 화면·테스트 코드를 생성하거나 검증된 모듈 ZIP을 설치하지 않는다. 나중에 재사용할 수 있는 배포 모듈로 등록하려면 실제 artifact digest와 테스트 결과가 있어야 한다.

| Admin 모듈 | 계획할 데이터·동작 | 필수 권한·검증 |
| --- | --- | --- |
| SEO_ADMIN | PageSeo, SiteSeo, OG override, SNS 공유 미리보기, metadata 발행 | 편집/발행 분리, 렌더 HTML·URL 검사 |
| INQUIRY_ADMIN | 문의 목록·상세·상태·처리 이력 | 담당자 권한, 개인정보 최소 노출 |
| PAGE_ADMIN | 페이지·slug·draft/published revision | 충돌 검출, 공개 버전 선택 |
| IMAGE_ADMIN | StorageAsset·대체 텍스트·이미지 참조 | 업로드 유형/크기·소유권·사용 중 삭제 검사 |
| POPUP_ADMIN / BANNER_ADMIN | 기간·노출 위치·링크·게시 상태 | 외부 URL 정책, 예약 시각·미리보기 |
| CONTENT_ADMIN | 게시물·분류·작성/검수/게시 revision | 저장형 XSS·권한·페이지 SEO 연결 |
| REDIRECT_ADMIN | source/destination·301/302·활성 상태 | 순환·오픈 리다이렉트·중복 검사 |
| MARKETING_ADMIN | 연결 ID·정책·환경·활성 요청·이벤트 매핑 | 임의 script 입력 금지, 동의/소유권 분리 |
| ANALYTICS_ADMIN | 허용 지표·집계·연결 확인 증거 | 개인 원문 제외, 접근 범위·보관 정책 |

## 프리셋과 원문 근거

프리셋은 `basic`, `seo`, `marketing`, `consultation`, `content`, `custom`이다. `SEO_BASIC`은 모든 프리셋의 기본 계획에 포함한다. **기본 포함은 실행이나 고객 필수 범위 확정을 뜻하지 않는다.** 근거가 없는 항목은 `internal_proposal`, 근거가 있는 항목도 `client_candidate`이며 모두 고객 확인이 필요하다. 정확히 존재하는 인용문도 의미상 동의의 자동 증명이 아니다.

아래는 합성 예시다. 실제 고객 자료로 바꾸기 전 테스트용으로만 사용한다.

```json
{
  "project_id": "PROJECT-1",
  "sources": [{"id": "SRC-1", "text": "회사 홈페이지에 검색 관리가 필요합니다."}],
  "preset": "seo",
  "modules": [{
    "id": "SEO_ADMIN",
    "source_refs": [{"source_id": "SRC-1", "quote": "검색 관리가 필요합니다."}],
    "secret_refs": []
  }],
  "ownership": [{"asset_id": "DOMAIN", "owner_ref": "CLIENT-1", "environment": "production"}]
}
```

선택 항목마다 `requirement → erd → migration → api → backend → admin → frontend → security → test → setup → delivery` 작업 명세를 만든다. 이는 모듈 자료 준비의 의존성 목록이며 전체 제작 공정의 실제 실행 스케줄러가 아니다. 인프라·검수 승인과 교차 모듈 의존성을 집행하는 실행기는 후속 구현이다.

자격증명 필드는 `secret-ref:EMAIL-PROD` 같은 저장소 식별자만 허용한다. 원문 키·비밀번호·임의 코드 설정을 받지 않는다. 참조가 존재한다는 것만으로 비밀 접근 권한이나 연결 성공을 증명하지 않는다. 해당 저장소의 권한 확인과 비밀 주입은 향후 실행 어댑터의 책임이다.

## 사용자가 고르는 동의 정책

프로젝트 담당자가 동의 정책의 적용 여부를 선택한다. 모든 프로젝트에 배너를 무조건 강제하지 않는다.

| `mode` | 담당자의 선택 | 계획 상태 |
| --- | --- | --- |
| `undecided` | 아직 결정하지 않음 | 배너 여부 미결정, 추적 실행 꺼짐 |
| `required` | 해당 범위에서 동의를 받기로 결정 | 배너/선호 설정 및 방문자 선택 처리 작업 필요 |
| `not_required` | 해당 범위에서 동의를 받지 않는 정책을 선택 | 이유·근거 기록과 적용 조건 검토 필요 |

```json
{
  "mode": "not_required",
  "selected_by": "STAFF-1",
  "selected_at": "2026-09-29T15:00:00+09:00",
  "reason": "이 프로젝트의 적용 지역·기능·전송 항목을 검토할 담당자 결정",
  "evidence_refs": []
}
```

이 객체를 `project-plan.json`의 `consent_policy`에 넣는다. 선택자·시각·이유·근거를 기록하며 결과의 `policy_verification_status`는 항상 `not_verified`다. `not_required`를 선택했다는 이유만으로 법적 요건이나 서비스 필수 조건 검사가 PASS가 되지 않는다. 이 문서는 어느 지역에서 동의가 면제되는지 판단하지 않는다.

**프로젝트 담당자의 설정, 적용 정책 검토, 개별 방문자의 동의는 별도 데이터다.** 모듈 선택은 동의가 아니며 분석·광고 모듈은 프리셋을 고른 뒤에도 `runtime_enabled=false`다. 현재는 모든 실행기가 미구현이므로 동의 설정과 무관하게 외부 실행이 꺼져 있다. 향후 동의가 필요한 정책에서는 동의 전·거부·철회 후 미발화와 환경별 동작을 검사한다. 면제 정책에서도 확인된 조건을 벗어나는 태그는 활성화하지 않는다.

## Event Contract

분석/마케팅 모듈이 선택되면 `page_view`, `contact_form_start`, `contact_form_submit`, `phone_click`, `kakao_click`, `portfolio_view`를 내부 후보로 제안한다. 현재 기본 매개변수는 `page_id`뿐이며 문의 내용·이름·이메일·전화번호·자유 입력 URL을 포함하지 않는다. 선택된 목적지 ID는 매핑 후보일 뿐 외부 전송이 아니다.

실제 EventContract는 승인한 이벤트 이름·목적·시점·매개변수 schema·Consent 분류·provider별 매핑·환경·보관 정책을 버전으로 묶어야 한다. `contact_form_submit`은 버튼 클릭이 아니라 저장 성공과 연결하고 중복 전송을 검증해야 한다. 이 실행·검증 기능은 아직 없다.

## SEO_BASIC와 OG

SEO_BASIC의 범위는 title, description, canonical, robots.txt, sitemap.xml, favicon, OG, Twitter 카드, 적합한 구조화 데이터, 404/redirect, Search Console 등록 준비다. 공개/비공개 환경의 robots와 sitemap 규칙을 분리한다. 등록 준비는 Search Console 계정 연결이나 실제 검색 노출 보장이 아니다. Google은 제목 생성에 여러 신호를 쓰며, sitemap 제출 역시 검색 노출 보장이 아니다. [Google 제목 안내](https://developers.google.com/search/docs/appearance/title-link), [sitemap 안내](https://developers.google.com/search/docs/crawling-indexing/sitemaps/overview).

현재 `resolve_seo_metadata()`는 **페이지 override → 콘텐츠 → 사이트 default** 순서로 설정값을 합치고 title·description·canonical, robots 값, OG 6개, Twitter 카드 종류의 후보를 만든다. OG 6개는 `og:title`, `og:description`, `og:image`, `og:url`, `og:type`, `og:site_name`이다. Open Graph 표준의 기본 필수 항목은 title/type/image/url 네 개이고 description/site_name은 추가 메타데이터다. 이 제품은 여섯 개를 기본 계약으로 정한다. [Open Graph 표준](https://ogp.me/).

이미지는 Storage의 `id`로만 선택한다. 후보가 선언한 URL·MIME·크기는 아직 실제 파일을 검사한 증거가 아니다. `1200×630`은 프로젝트의 기본 권장 규격이며 모든 SNS의 표시나 캐시 갱신을 보장하지 않는다. 어드민의 SNS 공유 미리보기 역시 예상 형태로 표시하고 실제 플랫폼 검증과 구분한다.

```json
{
  "site": {"title": "회사", "description": "회사 소개", "canonical": "https://example.com/", "og_image_ref": "IMAGE-1", "og_site_name": "회사"},
  "content": {},
  "page": {"title": "회사 소개", "canonical": "https://example.com/about"},
  "storage": [{"id": "IMAGE-1", "url": "https://cdn.example.com/og.png", "width": 1200, "height": 630, "mime": "image/png"}],
  "design_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

디자인 승인 → OG 이미지 후보 제작 → 고객/담당자의 후보 선택 → Storage에 저장 → 버전 참조 순서를 사용한다. 예시의 디자인 digest는 합성 식별자이며 승인 증거가 아니다. 실제 호출 서비스가 승인된 디자인 기록과 이미지 소유권을 확인해야 한다.

현재 URL 검사는 HTTPS·표준 포트·호스트 문법·자격증명/fragment/로컬 literal 주소 배제까지만 수행한다. DNS 질의·이미지 fetch·HTML 생성·렌더 결과 검사는 하지 않는다. 향후 이미지 검사기는 허용 Storage 호스트를 확인하고 DNS의 모든 IP 및 리다이렉트 홉을 재검사하여 loopback/private/link-local/metadata 주소를 거부해야 한다. DNS 재바인딩, 압축/디코딩 폭탄, 응답 바이트·시간·픽셀 한도, 실제 MIME을 검사한다. 단순 `https` 문자열 검사는 SSRF 방어 완료가 아니다.

검수 ID는 `SEO_RENDERED_HTML`, `SEO_URL_SAFETY`, `SEO_OG_IMAGE_FETCH`, `SEO_SITEMAP_ROBOTS`, `SEO_REDIRECT_404`, `SEO_STRUCTURED_DATA`, `SEO_SEARCH_CONSOLE_READY`다. metadata 설정 존재와 렌더 HTML 존재를 구분하고, 실제 HTML head·중복 태그·canonical 일치·JS 이전 응답·환경별 index 정책을 검사한다. JSON-LD의 맥락/유형/본문 일치, 없는 URL의 실제 404, redirect의 상태 코드/루프도 검증한다. 현재 이 검수들은 `not_invoked`로 남는다.

## 인수 조건과 후속 구현

순수 계획기는 원문 없음·잘못된 인용·알 수 없는 모듈·원문 비밀 설정·잘못된 정책 선택 기록을 거부한다. 계획 결과가 입력을 바꾸지 않는지, 동의 정책과 추적 실행이 분리되는지, stale snapshot이나 임의 승인 필드로 실행 가능 상태를 만들 수 없는지를 단위검사한다.

후속 작업은 인증된 프로젝트 설정 저장/수정/이력, 실제 Catalog UI, 검증된 모듈 패키지, 어드민 생성기, Consent runtime, provider 매핑, Storage/SEO 실제 검사, 고객 승인과 검수 집행이다. 이 문서와 순수 함수만으로 전체 홈페이지 납품이나 개인정보 정책 준수를 완료했다고 표시하지 않는다.
