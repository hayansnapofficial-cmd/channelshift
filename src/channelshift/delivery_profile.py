"""Read-only delivery planning. This module never executes or approves a stage.

The first company-site profile is a specification, not a validated generator.
Completing intake fields is not authentication, approval, or delivery readiness.
"""
from __future__ import annotations

import copy
import json

PROFILE_ID = "standard-company-site/v1-draft"
INPUTS = {
    "company_copy": "회사 소개 문구",
    "portfolio_assets": "사용할 포트폴리오 자료와 사용 권한",
    "contact_details": "홈페이지에 공개할 연락처",
    "inquiry_fields": "문의 폼에서 받을 항목",
    "inquiry_retention": "문의 내용 보관·삭제 기준",
    "admin_access": "문의 관리 담당자와 접근 방식",
    "brand_direction": "색상·문체·대표 화면 방향",
    "delivery_owner": "업무 확인·납품 담당자",
}

# This tuple's order is for presentation, not implicit sequential execution.
# Keep its six-field shape and existing IDs for saved intervention records.
# DEPENDENCIES defines the two design lanes and cross-cutting security gates.
STAGES = (
    ("intake", "클라이언트 요구사항 접수", "고객의 원문을 먼저 등록하세요.", "client_sources", "intake", "none"),
    ("environment_check", "프로젝트 접수·환경 점검", "원문을 확인한 뒤 자료·담당자·실행 환경을 정리하세요.", "project_brief", "intake", "none"),
    ("requirements", "요구사항 정리", "필요한 기능과 고객의 완료 조건을 정리하세요.", "requirements_and_acceptance", "specification", "none"),
    ("business_review", "업무 요구사항 확인", "고객 원문·업무 규칙·미확정 사항·인수 조건을 확인합니다.", "business_contract", "gate", "business"),
    ("security_requirements", "보안 요구사항·데이터 분류", "고객의 인용 근거와 내부 보안 기준을 구분하고 확인할 정책을 정리합니다.", "security_requirements", "specification", "none"),
    ("domain_model", "도메인 모델 설계", "업무 개념·관계·상태 전이를 먼저 정리합니다.", "domain_model", "specification", "none"),
    ("threat_model", "위협 모델·신뢰 경계", "공격자·자산·입력 경로·신뢰 경계와 대응 검사를 정의합니다.", "threat_model", "specification", "none"),
    ("erd", "ERD 설계", "도메인 모델을 저장 구조·관계·소유권·제약으로 구체화합니다.", "data_model", "specification", "none"),
    ("authorization_policy", "공통 접근 정책·공급자 검토", "누가 어떤 자원에 어떤 동작을 할 수 있는지 정의하고 공급자 능력을 확인합니다.", "authorization_policy", "specification", "none"),
    ("database_schema", "DB 스키마 설계", "ERD의 타입·키·제약·인덱스를 실제 DB 명세로 정의합니다.", "database_schema", "specification", "none"),
    ("api_contract", "API 계약 설계", "화면 동작에 필요한 요청·응답·권한·오류를 정의합니다.", "api_contract", "specification", "none"),
    ("backend_architecture", "백엔드 구조 설계", "서비스 책임·트랜잭션·인가·외부 연동 경계를 설계합니다.", "backend_architecture", "specification", "none"),
    ("system_review", "시스템 설계 검수", "업무 계약과 도메인·ERD·DB·API·백엔드 구조의 연결을 검토합니다.", "system_contract", "gate", "risk_based"),
    ("wireframe", "화면 명세·와이어프레임", "필요한 화면·동선·정상·오류·빈 상태와 화면 동작을 정의합니다.", "screen_spec", "specification", "none"),
    ("design_candidate", "디자인 후보 제작", "화면 명세와 현재 API 계약을 기준으로 비교할 디자인 후보를 만듭니다.", "design_candidate", "specification", "none"),
    ("design_preview", "디자인 미리보기 검수", "격리된 후보 화면을 PC·태블릿·모바일에서 확인하고 수정 차이를 비교합니다.", "design_preview_evidence", "verification", "none"),
    ("design_review", "디자인 선택·승인", "기준 버전에 연결된 화면·UX 후보를 선택하고 수정 또는 승인합니다.", "design_contract", "gate", "design"),
    ("contract_review", "G2A 세 계약 정합성 검사", "업무·시스템·승인 디자인의 같은 기준 버전에서 누락과 충돌을 검사합니다.", "contract_evidence", "gate", "none"),
    ("security_design_review", "SG1 보안 설계 검수", "세 계약과 위협·인증·인가·데이터 정책의 현재 버전을 독립 검토합니다.", "security_contract", "gate", "risk_based"),
    ("database_build", "개발 DB 구축", "격리된 개발 DB에 마이그레이션과 합성 자료를 적용합니다.", "database_candidate", "implementation", "none"),
    ("database_policy_tests", "격리 DB 접근 정책 검사", "실제 격리 DB에서 일반·관리자·우회 역할의 허용·거부와 권한 변경을 검사합니다.", "database_policy_evidence", "verification", "none"),
    ("database_review", "G2 DB 검수", "설치·제약·업그레이드 검사 결과와 독립 리뷰를 확인합니다.", "database_evidence", "gate", "risk_based"),
    ("backend_build", "백엔드 구현", "승인된 계약에 맞게 저장·권한·업무 기능을 구현합니다.", "backend_candidate", "implementation", "none"),
    ("backend_security_tests", "백엔드 보안 검사", "인증·객체 권한·테넌트 경계·입력·업로드·세션의 실패 시나리오를 검사합니다.", "backend_security_evidence", "verification", "none"),
    ("backend_review", "G3 백엔드 검수", "실제 API 동작·권한·중복 처리 검사와 독립 리뷰를 확인합니다.", "backend_evidence", "gate", "risk_based"),
    ("frontend_build", "프론트 구축", "승인된 디자인 계약을 실제 백엔드 API에 연결합니다.", "frontend_candidate", "implementation", "none"),
    ("security_implementation_review", "SG2 구현 보안 검수", "프론트·백엔드의 코드·의존성·비밀값 검사와 실제 권한 검사 증거를 검토합니다.", "security_implementation_evidence", "gate", "risk_based"),
    ("acceptance", "통합·화면 검수", "세 계약을 기준으로 실제 기능·데이터·권한·콘텐츠·반응형 화면을 검증합니다.", "acceptance_evidence", "verification", "none"),
    ("security_release_review", "SG3 배포 보안 검수", "동일 후보의 DAST·보안 E2E·설정·의존성과 미해결 지적을 독립 검토합니다.", "security_release_evidence", "gate", "risk_based"),
    ("release_review", "G4 납품 후보 검수", "검수된 같은 빌드와 운영 인계자료를 확인합니다.", "release_candidate", "gate", "acceptance"),
    ("deployment_approval", "배포 권한 확인", "배포 담당자가 릴리스·대상 환경·작업·유효기간을 확인합니다.", "deployment_decision", "gate", "deployment"),
    ("deployment", "배포·동작 확인", "승인된 빌드를 지정 환경에 배포하고 동작을 확인합니다.", "deployment_receipt", "implementation", "none"),
    ("operations_security", "운영 보안·대응 준비", "감사 로그·알림 수신·비밀값 회전·취약점 대응과 복구 담당자를 확인합니다.", "operations_security_evidence", "verification", "none"),
    ("handover", "인수인계·납품", "운영 안내와 권한 소유관계를 전달하고 인수 근거를 남깁니다.", "handover_record", "gate", "customer_acceptance"),
)

DEPENDENCIES = {
    "intake": (),
    "environment_check": ("intake",),
    "requirements": ("environment_check",),
    "business_review": ("requirements",),
    "security_requirements": ("business_review",),
    "domain_model": ("business_review",),
    "threat_model": ("security_requirements", "domain_model"),
    "erd": ("domain_model",),
    "authorization_policy": ("threat_model", "erd"),
    "database_schema": ("erd", "authorization_policy"),
    "api_contract": ("database_schema", "authorization_policy"),
    "backend_architecture": ("api_contract",),
    "system_review": ("backend_architecture",),
    "wireframe": ("business_review",),
    "design_candidate": ("wireframe", "api_contract"),
    "design_preview": ("design_candidate",),
    "design_review": ("design_preview",),
    "contract_review": ("system_review", "design_review"),
    "security_design_review": ("contract_review", "threat_model", "authorization_policy"),
    "database_build": ("contract_review", "security_design_review"),
    "database_policy_tests": ("database_build",),
    "database_review": ("database_build", "database_policy_tests"),
    "backend_build": ("database_review",),
    "backend_security_tests": ("backend_build", "database_policy_tests"),
    "backend_review": ("backend_build", "backend_security_tests"),
    "frontend_build": ("backend_review",),
    "security_implementation_review": ("frontend_build", "backend_security_tests", "database_policy_tests"),
    "acceptance": ("frontend_build", "security_implementation_review"),
    "security_release_review": ("acceptance", "security_implementation_review"),
    "release_review": ("acceptance", "security_release_review"),
    "deployment_approval": ("release_review", "security_release_review"),
    "deployment": ("deployment_approval",),
    "operations_security": ("deployment", "security_release_review"),
    "handover": ("deployment", "operations_security"),
}

_SYSTEM_STAGES = ("domain_model", "erd", "database_schema", "api_contract", "backend_architecture", "system_review")
_DESIGN_STAGES = ("wireframe", "design_candidate", "design_preview", "design_review")
_INTAKE_STAGES = ("intake", "environment_check", "requirements", "business_review")
_IMPLEMENTATION_STAGES = ("database_build", "database_review", "backend_build", "backend_review", "frontend_build",
                          "acceptance", "release_review", "deployment_approval", "deployment", "handover")
_SECURITY_DESIGN_STAGES = ("security_requirements", "threat_model", "authorization_policy", "security_design_review")
_SECURITY_VERIFICATION_STAGES = ("database_policy_tests", "backend_security_tests", "security_implementation_review",
                                  "security_release_review", "operations_security")
_SECURITY_GATES = {"security_design_review": "SG1", "security_implementation_review": "SG2",
                   "security_release_review": "SG3"}
STAGE_LANES = {**{stage: "shared" for stage in (*_INTAKE_STAGES, "contract_review")},
               **{stage: "system" for stage in _SYSTEM_STAGES},
               **{stage: "design" for stage in _DESIGN_STAGES},
               **{stage: "security" for stage in (*_SECURITY_DESIGN_STAGES, *_SECURITY_VERIFICATION_STAGES)},
               **{stage: "implementation" for stage in _IMPLEMENTATION_STAGES}}


def _contracts():
    """Definitions of required future records, not existing approved contracts."""
    return {
        "business": {
            "title": "업무 계약", "artifact": "business_contract", "review_stage": "business_review",
            "components": ["고객 원문", "기능·사용자", "업무 규칙·권한", "미확정 사항", "인수 조건"],
            "required_binding": ["source_digest", "requirements_revision"],
            "implementation_requires": "approved_revision",
        },
        "system": {
            "title": "시스템 계약", "artifact": "system_contract", "review_stage": "system_review",
            "components": ["도메인 모델", "ERD", "DB 스키마", "API 계약", "백엔드 구조"],
            "required_binding": ["business_contract_revision", "domain_model_revision", "erd_revision",
                                 "database_schema_revision", "api_contract_revision", "backend_architecture_revision"],
            "implementation_requires": "accepted_revision",
        },
        "design": {
            "title": "디자인 계약", "artifact": "design_contract", "review_stage": "design_review",
            "components": ["화면 명세", "선택한 디자인 후보", "반응형 화면·UX", "미리보기 검수 증거"],
            "required_binding": ["business_contract_revision", "system_contract_revision", "screen_spec_revision",
                                 "candidate_revision", "preview_build_digest"],
            "implementation_requires": "approved_revision",
        },
    }


def _security_contract():
    """Cross-cutting requirements, never a fabricated security assessment."""
    return {
        "status": "planned", "cross_cutting": True, "artifact": "security_contract",
        "review_stage": "security_design_review", "covers_contracts": ["business", "system", "design"],
        "components": ["security_requirements", "data_classification", "threat_model", "authentication_and_session",
                       "authorization_policy", "privacy_and_retention", "upload_and_storage", "secret_boundary",
                       "api_security", "audit_and_response"],
        "required_binding": ["source_digest", "requirements_revision", "business_contract_revision",
                             "system_contract_revision", "design_contract_revision", "threat_model_revision",
                             "authorization_policy_revision", "provider_capability_digest", "security_policy_digest"],
        "requirement_origins": ["client_source", "internal_security_baseline"],
        "client_origin_requires": ["source_id", "source_revision", "start", "end", "exact_quote"],
        "internal_origin_requires": ["baseline_reference", "version", "rationale", "applicability_decision"],
        "gate_ids": dict(_SECURITY_GATES),
        "implementation_requires": "reviewed_current_revision",
        "evidence_status": "not_invoked", "enforcement_connected": False,
    }


def _provider_contracts():
    """Proposed adapter boundaries; no provisioning, credentials or connections."""
    return {
        "authorization": {
            "status": "planned", "provider_neutral": True, "default_decision": "deny",
            "dimensions": ["principal", "resource", "action", "ownership", "tenant", "condition"],
            "capability_mismatch": "HOLD", "unknown_claim_provenance": "HOLD",
            "application_and_database_checks_required": True,
            "sql_grants_and_row_policies_separate": True,
            "compilation": "not_invoked", "simulation_is_evidence": False,
        },
        "database": {
            "status": "not_connected", "selection": "unselected",
            "proposed_providers": ["postgresql", "supabase"],
            "capabilities_verified": False, "provisioning": "not_invoked",
            "required_evidence": ["provider_capabilities", "migration_digest", "sql_grants",
                                  "row_policies", "isolated_database_policy_tests", "privileged_role_tests",
                                  "claim_provenance_and_pool_isolation"],
            "policy_test_execution": "not_invoked",
        },
        "source": {
            "status": "not_connected", "proposed_provider": "github_app",
            "repository_binding_verified": False, "webhook_receiver_implemented": False,
            "checks": "not_invoked", "same_commit_required": True,
            "required_evidence": ["installation_and_repository_binding", "least_privilege_permissions",
                                  "required_check_producers", "head_and_base_commit", "candidate_build_digest",
                                  "signed_webhook_deduplication", "canonical_state_reconciliation"],
            "head_change_effect": "STALE", "unknown_or_missing_check": "HOLD",
        },
    }


def _security_evidence_requirements():
    return {
        "security_design_review": ["security_requirements", "data_classification", "threat_model",
                                   "authorization_policy", "provider_capabilities", "three_contract_binding",
                                   "independent_security_review"],
        "database_policy_tests": ["isolated_database_receipt", "migration_and_policy_digests",
                                  "sql_grants_and_row_policies", "allow_and_deny_matrix",
                                  "admin_and_bypass_roles", "claim_provenance_and_pool_isolation"],
        "backend_security_tests": ["authentication_and_session", "object_and_tenant_authorization",
                                   "input_and_upload_validation", "privacy_safe_errors_and_logs",
                                   "rate_limit_policy_tests"],
        "security_implementation_review": ["sast", "dependency_scan", "secret_scan",
                                           "database_policy_evidence", "backend_security_evidence",
                                           "frontend_security_tests", "independent_security_review"],
        "security_release_review": ["same_commit_and_build", "dast", "security_e2e", "config_scan",
                                    "dependency_rescan", "finding_disposition", "independent_security_review",
                                    "deployment_environment_binding"],
        "operations_security": ["audit_log_redaction", "alert_delivery_check", "response_owner",
                                "secret_rotation_runbook", "recovery_runbook", "vulnerability_response_plan"],
    }


def _delivery_evidence_requirements():
    """Required future observations, with applicability decided in the contract."""
    return {
        "acceptance": ["SEO_RENDERED_HTML", "SEO_URL_SAFETY", "SEO_OG_IMAGE_FETCH",
                       "SEO_SITEMAP_ROBOTS", "SEO_REDIRECT_404", "SEO_STRUCTURED_DATA"],
        "deployment": ["DNS_ZONE_SNAPSHOT", "DNS_PLAN_DIFF", "DNS_APPROVAL", "DNS_READBACK",
                       "DNS_MAIL_PRESERVED", "DOMAIN_TLS_HTTPS", "DOMAIN_REDIRECT_HEALTH"],
        "handover": ["SEO_SEARCH_CONSOLE_READY", "DELIVERY_OWNERSHIP", "DELIVERY_BACKUP_RESTORE",
                     "DELIVERY_SECRET_HANDOFF", "DELIVERY_TEMP_ACCESS_REVOKED",
                     "DELIVERY_CUSTOMER_ACCEPTANCE"],
    }


def standard_site_profile():
    """Return a fresh profile; no execution capability is implied by this data."""
    stages = []
    for stage_id, title, action, artifact, kind, decision in STAGES:
        lane = STAGE_LANES[stage_id]
        area = ("requirements" if stage_id in (*_INTAKE_STAGES, "security_requirements")
                else "system_design" if lane == "system" or stage_id in _SECURITY_DESIGN_STAGES
                else "design_studio" if lane == "design" else "production_review")
        stages.append({
            "id": stage_id, "title": title, "action": action,
            "depends_on": list(DEPENDENCIES[stage_id]),
            "artifact": artifact, "kind": kind,
            "lane": lane, "ui_area": area,
            "human_decision": decision,
            "independent_review": stage_id in {"system_review", "database_review", "backend_review", "release_review", *_SECURITY_GATES},
            "requires_contracts": ["business", "system", "design"]
                                  if stage_id in ("contract_review", "security_design_review", *_IMPLEMENTATION_STAGES,
                                                   *_SECURITY_VERIFICATION_STAGES) else [],
            "requires_security_contract": stage_id in (*_IMPLEMENTATION_STAGES, *_SECURITY_VERIFICATION_STAGES),
            "requires_original_source": stage_id != "intake",
            "execution_status": "not_invoked",
        })
    by_id = {stage["id"]: stage for stage in stages}
    by_id["wireframe"]["concept"] = "screen_spec"
    by_id["design_candidate"]["input_revisions"] = ["business_contract", "screen_spec", "api_contract"]
    by_id["contract_review"]["require_current_revision_binding"] = True
    for stage_id, gate_id in _SECURITY_GATES.items():
        by_id[stage_id]["gate_id"] = gate_id
        by_id[stage_id]["require_current_revision_binding"] = True
    for stage_id, evidence in _security_evidence_requirements().items():
        by_id[stage_id]["required_evidence"] = evidence
        by_id[stage_id]["evidence_status"] = "not_invoked"
    for stage_id, evidence in _delivery_evidence_requirements().items():
        by_id[stage_id]["required_evidence"] = evidence
        by_id[stage_id]["evidence_status"] = "not_invoked"
        by_id[stage_id]["evidence_contracts"] = ["docs/PROJECT_CATALOG.md", "docs/INFRASTRUCTURE_DELIVERY.md"]
        by_id[stage_id]["applicability_requires"] = "recorded_contract_decision"
    return {
        "id": PROFILE_ID,
        "status": "draft_unvalidated",
        "workflow_kind": "dag",
        "workflow_revision": 3,
        "parallel_lanes": ["system", "design"],
        "cross_cutting_lanes": ["security"],
        "contracts_are_definitions": True,
        "contracts": _contracts(),
        "security_contract": _security_contract(),
        "provider_contracts": _provider_contracts(),
        "security_execution": {"mode": "planning_only", "runtime_connected": False,
                               "checks": "not_invoked", "policy_compilation": "not_invoked",
                               "isolated_database_tests": "not_invoked", "webhook_processing": "not_invoked"},
        "ui_areas": [
            {"id": area, "title": title, "stage_ids": [stage["id"] for stage in stages if stage["ui_area"] == area]}
            for area, title in (("requirements", "요구사항"), ("system_design", "시스템 설계"),
                                ("design_studio", "디자인 스튜디오"), ("production_review", "제작·검수"))
        ],
        "scope": ["회사 소개", "포트폴리오", "문의 접수", "관리자 로그인", "문의 목록·상태 관리"],
        "scope_is_proposal": True,
        "first_slice": ["소개 화면", "문의 화면", "관리자 문의 목록"],
        "stack": {"status": "unselected", "source": "verified_existing_project_or_approved_template"},
        "max_attempts_proposed": 3,
        "unapproved_provider_fallback": False,
        "execution_binding": {
            "provider": "codex",
            "auth": "chatgpt_managed",
            "account": "operator_owned",
            "location": "local_companion",
            "transport_proposed": "app_server_stdio",
            "api_key_fallback": False,
            "on_limit": "WAITING_FOR_QUOTA",
            "status": "not_connected",
        },
        "stages": stages,
    }


def delivery_plan(brief=None):
    """Validate a small intake brief and return the planned sequence, not a run.

    Descriptions are untrusted text. No command, path, URL or provider is executed.
    Empty fields generate missing-input guidance instead of fabricated decisions.
    """
    if brief is None:
        brief = {"format": "channelshift.delivery-brief/v1", "project_name": "", "goal": "", "inputs": {}}
    required = {"format", "project_name", "goal", "inputs"}
    if type(brief) is not dict or not required <= set(brief) <= required | {"client_request"}:
        raise ValueError("invalid_delivery_brief")
    if brief["format"] != "channelshift.delivery-brief/v1":
        raise ValueError("invalid_delivery_brief")
    if type(brief["inputs"]) is not dict or not set(brief["inputs"]) <= set(INPUTS):
        raise ValueError("invalid_delivery_brief")
    values = [(brief["project_name"], 200), (brief["goal"], 4000)]
    values.append((brief.get("client_request", ""), 12000))
    values += [(value, 4000) for value in brief["inputs"].values()]
    if any(type(value) is not str or len(value) > limit for value, limit in values):
        raise ValueError("invalid_delivery_brief")
    try:
        if len(json.dumps(brief, ensure_ascii=False).encode("utf-8")) > 65536:
            raise ValueError("invalid_delivery_brief")
    except (UnicodeError, TypeError):
        raise ValueError("invalid_delivery_brief") from None
    missing = []
    source_present = bool(brief.get("client_request", "").strip())
    if not source_present:
        missing.append({"field": "client_request", "label": "고객 요구사항 원문"})
    for key, title in (("project_name", "프로젝트명"), ("goal", "홈페이지의 목적")):
        if not brief[key].strip():
            missing.append({"field": key, "label": title})
    for key, title in INPUTS.items():
        if not brief["inputs"].get(key, "").strip():
            missing.append({"field": "inputs." + key, "label": title})
    return {
        "format": "channelshift.delivery-plan/v1-draft",
        "mode": "planning_only",
        "runtime_connected": False,
        "natural_language_processing": "not_invoked",
        "source_present": source_present,
        "blocked_stages": [] if source_present else [stage[0] for stage in STAGES if stage[0] != "intake"],
        "profile": standard_site_profile(),
        "brief": copy.deepcopy(brief),
        "missing_inputs": missing,
        "next_action": "고객의 요구사항 원문을 먼저 등록하세요." if not source_present else "누락 자료를 입력하세요." if missing else "담당자가 입력 내용을 확인하고 요구사항 초안을 작성하세요.",
        "notice": "계획 조회 결과입니다. 입력을 채워도 검사·승인·실행·납품이 완료된 것은 아닙니다.",
    }
