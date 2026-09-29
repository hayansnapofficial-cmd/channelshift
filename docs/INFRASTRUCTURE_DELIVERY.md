# 인프라 연결과 실제 납품

상태: 2026-09-29 V1 인터페이스·검수 계약과 읽기 전용 DNS 계획기. DNS·GitHub·DB·Storage·Hosting의 실제 변경, 인증서 발급, 공개 배포와 고객 계정 인계는 구현하지 않았다.

V1의 목표는 고객 원문에서 나온 요구사항과 승인된 설계로 웹사이트를 만들고, 보안·기능·SEO·품질 검수 후 실제 도메인에서 확인하여 고객에게 인계하는 것이다. [8개 업무 모듈](PROJECT_CATALOG.md)의 Infrastructure와 Delivery는 이 마지막 단계를 담당한다.

## 환경·공급자·권한 경계

`dev`, `staging`, `production`은 연결 대상, 데이터, SecretRef, 권한, 공개 정책을 분리한다. 운영 DB나 DNS 관리자 계정을 모델에 직접 전달하지 않는다. 작업별 최소 scope의 provider binding, 만료시간, 권한 소유자를 기록한다.

| 자원 | 승인된 변경 계획에 묶을 정보 | 실제 검증 |
| --- | --- | --- |
| GitHub | 저장소 ID·branch·base/head commit·PR·변경 manifest | 동일 commit의 검사와 보호 규칙 |
| Database | DB/tenant·schema 기준 버전·migration digest·복구 계획 | 격리 DB, drift, migration, RLS/권한, 백업 복원 |
| Storage | bucket·정책·허용 유형/용량·자원 소유권 | 업로드·다운로드 권한 및 파일 검사 |
| Hosting | 계정·프로젝트·환경·build digest·region | 배포 readback·health·오류 관측 |
| Domain/DNS | registrar와 DNS provider 구분·zone ID·snapshot·diff | 실제 DNS 응답·메일 보존·전파 관측 |
| TLS | 도메인 목록·인증서·redirect 정책 | HTTPS·이름/체인/만료·HTTP redirect |

검수 통과는 배포 승인과 다르다. 승인된 작업·대상·release/plan digest·권한자·유효기간을 실행 직전에 다시 확인해야 한다. 오래된 snapshot, 변경된 운영 대상, 철회된 권한이면 재검수한다. 생성된 문자열의 `approved: true`나 사용자가 입력한 역할 이름은 권한 증거가 아니다.

## DNS Provider 계약

공통 인터페이스는 다음과 같다. 모두 향후 실제 어댑터가 구현할 계약이다.

| 인터페이스 | 계약 |
| --- | --- |
| `inspectZone(binding)` | 공급자 원본 export와 정규화 레코드, 조회 계정·zone·시각·digest 반환 |
| `createChangePlan(snapshot, desired)` | 보존/추가/수정/삭제 및 NS 변경 후보를 명시 |
| `validateChangePlan(plan, release, policy)` | 원본 전체성·메일/서브도메인·충돌·대상·scope·복구 조건 검사 |
| `applyApprovedChanges(planDigest, approvalRef)` | 승인 기록을 검증하고 현재 zone 일치 확인 후 제한된 변경 |
| `verifyDns(expected, observationPolicy)` | 권한 DNS와 외부 관측의 응답·전파·TLS·웹 health 증거 반환 |
| `rollback(restorePlan, approvalRef)` | 공급자별 복원 가능 여부를 검토한 별도 변경; 무조건 자동 원복 아님 |

`Cloudflare`, `Gabia`, `Cafe24`, `ManualDns`를 구분한다. API로 지원되는지 검증하지 않은 기능을 지원한다고 선언하지 않는다. 현재 Python 계획기는 알려진 네 공급자 이름에 모두 `MANUAL_ACTION_REQUIRED`를 반환하며 실제 어댑터는 없다. 이는 해당 업체의 API 제공 여부에 대한 판단이 아니다. 알 수 없는 공급자는 `unsupported_provider`로 중단한다.

수동 절차도 같은 프로젝트에 담당자·계획 버전·진행 상태·증거를 남긴다. 사람이 “설정 완료”를 눌렀다는 사실만으로 DNS 검사를 PASS로 만들지 않는다. 독립적인 실제 조회와 웹 확인이 필요하다.

## DNS 계획기의 현재 기능

```powershell
python -m channelshift dns-plan --input dns-plan.json
```

합성 입력 예시:

```json
{
  "project_id": "PROJECT-1",
  "sources": [{"id": "SRC-1", "text": "example.com과 www를 연결하고 기존 회사 이메일은 유지해주세요."}],
  "source_refs": [{"source_id": "SRC-1", "quote": "기존 회사 이메일은 유지해주세요."}],
  "domain": "example.com",
  "provider": "manual",
  "snapshot": {"records": [
    {"id": "WEB", "type": "A", "name": "@", "value": "1.1.1.1", "ttl": 300},
    {"id": "MAIL", "type": "MX", "name": "@", "value": "10 mail.example.com", "ttl": 3600},
    {"id": "SPF", "type": "TXT", "name": "@", "value": "v=spf1 -all", "ttl": 3600}
  ]},
  "desired_records": [
    {"id": "WEB", "type": "A", "name": "@", "value": "8.8.8.8", "ttl": 300},
    {"id": "WWW", "type": "CNAME", "name": "www", "value": "example.com", "ttl": 300}
  ]
}
```

예시 IP는 테스트용이며 실제 웹 호스팅 목적지가 아니다. 호스팅 업체가 확인한 값을 사용해야 한다.

현재 함수는 원문 인용과 제한된 레코드 shape을 검증하고 snapshot digest와 계획 digest를 만든다. apex/www의 A/AAAA/CNAME 추가·동일 ID/이름/타입 값 변경만 계획한다. 나머지 snapshot 레코드는 그대로 보존한다. 메일·검증 TXT·서브도메인을 암묵적으로 삭제하지 않는다. CNAME 공존 충돌, 메일 레코드 ID 전용, private IP, 임의 삭제·NS 변경 입력은 거부한다.

기존 MX·SPF·DKIM·DMARC 및 verification/임의 레코드의 **의미**를 자동 증명하지 않는다. 전달된 값의 보존 계획이다. 전체 zone에 DKIM selector가 누락되지 않았는지, 공급자 proxy·priority·routing metadata가 모두 보존되는지는 실제 snapshot adapter가 확인해야 한다. 현재 간단한 record 모델은 provider 원본 export의 대체물이 아니다. 실제 적용 전 손실 없는 원본 export의 StorageRef와 digest를 별도로 보존하도록 계약을 확장해야 한다.

현재 출력은 `snapshot_verified=false`, `approved=false`, `execution_enabled=false`, `verification_status=not_invoked`다. 새로운 빈 zone과 provider별 특수 CNAME flattening, wildcard, DNSSEC/DS, NS 이관, 레코드 타입 전환·삭제는 별도 수동 검토/계약 확장이 필요하다. 이 제한을 우회해 계획을 바로 API에 전달하지 않는다.

## 변경과 검증 순서

1. 고객 요청과 대상 도메인의 소유권을 확인한다. registrar·DNS provider·hosting을 각각 기록한다.
2. zone 전체 snapshot과 별도 원본 export를 저장하고 메일, 검증, 웹, 기타 서브도메인을 분류한다.
3. diff와 보존 목록, NS 이관 여부, DNSSEC·전파·복구 조건을 제시한다.
4. 같은 snapshot/plan/release/target에 대한 독립 검사와 사람 승인을 받는다.
5. 실행 직전 drift와 권한을 재검사한다. 지원 어댑터 또는 추적 가능한 수동 절차로 변경한다.
6. 실제 DNS readback과 외부 관측, HTTPS/TLS, redirect, 메일 레코드 보존, 웹 health를 확인한다.
7. 결과가 기대와 다르면 실패/대기 상태로 남기고 복구 계획을 적용한다. API 성공만으로 `DOMAIN_READY`를 발급하지 않는다.

증거 ID는 `DNS_ZONE_SNAPSHOT`, `DNS_PLAN_DIFF`, `DNS_APPROVAL`, `DNS_READBACK`, `DNS_MAIL_PRESERVED`, `DOMAIN_TLS_HTTPS`, `DOMAIN_REDIRECT_HEALTH`다. 관측 결과에 DNS resolver·시각·TTL·기대/실제 값·인증서·응답 코드를 기록한다. 모든 지역 전파나 이메일 수신 성공을 레코드 일치 검사만으로 보장하지 않는다.

## Release Gate와 실제 도메인

Release Gate는 Functional, Security, Performance, Accessibility, SEO, Responsive, Browser 증거를 같은 release digest에 연결한다. 적용하지 않는 검사는 이유·권한자의 예외 결정과 유효기간이 필요하다. Security에는 적용 범위에 맞는 Threat Model, SAST, SCA, Secret scan, DAST, RLS/역할 테스트, Security E2E가 포함된다.

Preview에서 통과했다고 운영 도메인이 통과한 것은 아니다. 실제 운영 도메인의 canonical·robots·sitemap·OG·404·redirect와 TLS·health를 확인한다. OG 이미지는 승인된 디자인 후보와 Storage 자원에 연결하고 URL/이미지 fetch를 제한한다. [SEO/OG 계약](PROJECT_CATALOG.md)의 검수 ID를 사용한다. 현재 실제 검사 runner와 Release Gate 집행은 구현하지 않았다.

배포 후에는 오류·health·로그·용량·인증서 만료 관측과 담당자 알림 경로를 준비한다. 개인정보·비밀을 로그에 남기지 않으며 장애 대응 runbook과 고객 연락 창구를 연결한다. 감시가 켜졌다는 표시에는 실제 연결·시험 증거가 필요하다.

## 소유권과 납품

프로젝트 시작부터 Domain, DNS, GitHub, Database, Storage, Hosting, Analytics, Search Console, Email 각각의 고객/제작사 소유 관계, 실제 계정 참조, 관리 역할, 비용 부담, 해지/이관 조건을 기록한다. 계획기의 `owner_ref`는 확인 대상을 가리키며 계정 소유권 검증 자체가 아니다.

| 납품 항목 | 필요한 증거 |
| --- | --- |
| 실제 도메인 | 승인 release에 대한 DOMAIN/TLS/SEO/health readback |
| 계정·자산 | 고객이 접근 가능한 권한과 소유권 확인; 단순 계정명 기록으로 대체 불가 |
| 관리자 | 고객 관리자 로그인·권한·복구 경로 검사; 제작자 암호 공동 사용 금지 |
| DB·Storage | 운영 위치·접근 정책·보관/삭제 정책·가용 용량·비용 안내 |
| 백업 | 백업 목록, 보존/암호화, 실제 복원 연습 결과와 복구 책임 |
| 비밀 전달 | 승인된 보안 채널/비밀 저장소로 전달·회전; 문서/Git/채팅 원문 제외 |
| 임시 권한 회수 | 제작사 토큰·초대·세션·임시 관리자 권한의 제거 및 readback |
| 운영 문서 | 실행/배포/장애/수정/계약 만료 절차와 고객 교육 |
| 고객 인수 | 동일 release/domain 및 남은 제한에 대한 고객 확인 |

증거 ID는 `DELIVERY_OWNERSHIP`, `DELIVERY_BACKUP_RESTORE`, `DELIVERY_SECRET_HANDOFF`, `DELIVERY_TEMP_ACCESS_REVOKED`, `DELIVERY_CUSTOMER_ACCEPTANCE`다. 필요한 증거가 빠지면 `DELIVERED`로 바꾸지 않는다. 납품 이후 수정은 ChangeRequest → 영향분석 → 새 후보 → 재검수 → 새 배포로 처리하며 이전 승인 기록을 재사용하지 않는다.

현재는 이 상태 전이와 공급자 연결을 집행하는 서버, 고객 인수 UI, 백업·복원 작업자가 없다. 본 계획기와 문서는 전체 납품 기능을 위한 검토 가능한 기반이다.
