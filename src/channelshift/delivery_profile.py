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
# DEPENDENCIES defines the two lanes and the join before implementation.
STAGES = (
    ("intake", "클라이언트 요구사항 접수", "고객의 원문을 먼저 등록하세요.", "client_sources", "intake", "none"),
    ("environment_check", "프로젝트 접수·환경 점검", "원문을 확인한 뒤 자료·담당자·실행 환경을 정리하세요.", "project_brief", "intake", "none"),
    ("requirements", "요구사항 정리", "필요한 기능과 고객의 완료 조건을 정리하세요.", "requirements_and_acceptance", "specification", "none"),
    ("business_review", "업무 요구사항 확인", "고객 원문·업무 규칙·미확정 사항·인수 조건을 확인합니다.", "business_contract", "gate", "business"),
    ("domain_model", "도메인 모델 설계", "업무 개념·관계·상태 전이를 먼저 정리합니다.", "domain_model", "specification", "none"),
    ("erd", "ERD 설계", "도메인 모델을 저장 구조·관계·소유권·제약으로 구체화합니다.", "data_model", "specification", "none"),
    ("database_schema", "DB 스키마 설계", "ERD의 타입·키·제약·인덱스를 실제 DB 명세로 정의합니다.", "database_schema", "specification", "none"),
    ("api_contract", "API 계약 설계", "화면 동작에 필요한 요청·응답·권한·오류를 정의합니다.", "api_contract", "specification", "none"),
    ("backend_architecture", "백엔드 구조 설계", "서비스 책임·트랜잭션·인가·외부 연동 경계를 설계합니다.", "backend_architecture", "specification", "none"),
    ("system_review", "시스템 설계 검수", "업무 계약과 도메인·ERD·DB·API·백엔드 구조의 연결을 검토합니다.", "system_contract", "gate", "risk_based"),
    ("wireframe", "화면 명세·와이어프레임", "필요한 화면·동선·정상·오류·빈 상태와 화면 동작을 정의합니다.", "screen_spec", "specification", "none"),
    ("design_candidate", "디자인 후보 제작", "화면 명세와 현재 API 계약을 기준으로 비교할 디자인 후보를 만듭니다.", "design_candidate", "specification", "none"),
    ("design_preview", "디자인 미리보기 검수", "격리된 후보 화면을 PC·태블릿·모바일에서 확인하고 수정 차이를 비교합니다.", "design_preview_evidence", "verification", "none"),
    ("design_review", "디자인 선택·승인", "기준 버전에 연결된 화면·UX 후보를 선택하고 수정 또는 승인합니다.", "design_contract", "gate", "design"),
    ("contract_review", "G2A 세 계약 정합성 검사", "업무·시스템·승인 디자인의 같은 기준 버전에서 누락과 충돌을 검사합니다.", "contract_evidence", "gate", "none"),
    ("database_build", "개발 DB 구축", "격리된 개발 DB에 마이그레이션과 합성 자료를 적용합니다.", "database_candidate", "implementation", "none"),
    ("database_review", "G2 DB 검수", "설치·제약·업그레이드 검사 결과와 독립 리뷰를 확인합니다.", "database_evidence", "gate", "risk_based"),
    ("backend_build", "백엔드 구현", "승인된 계약에 맞게 저장·권한·업무 기능을 구현합니다.", "backend_candidate", "implementation", "none"),
    ("backend_review", "G3 백엔드 검수", "실제 API 동작·권한·중복 처리 검사와 독립 리뷰를 확인합니다.", "backend_evidence", "gate", "risk_based"),
    ("frontend_build", "프론트 구축", "승인된 디자인 계약을 실제 백엔드 API에 연결합니다.", "frontend_candidate", "implementation", "none"),
    ("acceptance", "통합·화면 검수", "세 계약을 기준으로 실제 기능·데이터·권한·콘텐츠·반응형 화면을 검증합니다.", "acceptance_evidence", "verification", "none"),
    ("release_review", "G4 납품 후보 검수", "검수된 같은 빌드와 운영 인계자료를 확인합니다.", "release_candidate", "gate", "acceptance"),
    ("deployment_approval", "배포 권한 확인", "배포 담당자가 릴리스·대상 환경·작업·유효기간을 확인합니다.", "deployment_decision", "gate", "deployment"),
    ("deployment", "배포·동작 확인", "승인된 빌드를 지정 환경에 배포하고 동작을 확인합니다.", "deployment_receipt", "implementation", "none"),
    ("handover", "인수인계·납품", "운영 안내와 권한 소유관계를 전달하고 인수 근거를 남깁니다.", "handover_record", "gate", "customer_acceptance"),
)

DEPENDENCIES = {
    "intake": (),
    "environment_check": ("intake",),
    "requirements": ("environment_check",),
    "business_review": ("requirements",),
    "domain_model": ("business_review",),
    "erd": ("domain_model",),
    "database_schema": ("erd",),
    "api_contract": ("database_schema",),
    "backend_architecture": ("api_contract",),
    "system_review": ("backend_architecture",),
    "wireframe": ("business_review",),
    "design_candidate": ("wireframe", "api_contract"),
    "design_preview": ("design_candidate",),
    "design_review": ("design_preview",),
    "contract_review": ("system_review", "design_review"),
    "database_build": ("contract_review",),
    "database_review": ("database_build",),
    "backend_build": ("database_review",),
    "backend_review": ("backend_build",),
    "frontend_build": ("backend_review",),
    "acceptance": ("frontend_build",),
    "release_review": ("acceptance",),
    "deployment_approval": ("release_review",),
    "deployment": ("deployment_approval",),
    "handover": ("deployment",),
}

_SYSTEM_STAGES = ("domain_model", "erd", "database_schema", "api_contract", "backend_architecture", "system_review")
_DESIGN_STAGES = ("wireframe", "design_candidate", "design_preview", "design_review")
_INTAKE_STAGES = ("intake", "environment_check", "requirements", "business_review")
_IMPLEMENTATION_STAGES = ("database_build", "database_review", "backend_build", "backend_review", "frontend_build",
                          "acceptance", "release_review", "deployment_approval", "deployment", "handover")
STAGE_LANES = {**{stage: "shared" for stage in (*_INTAKE_STAGES, "contract_review")},
               **{stage: "system" for stage in _SYSTEM_STAGES},
               **{stage: "design" for stage in _DESIGN_STAGES},
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


def standard_site_profile():
    """Return a fresh profile; no execution capability is implied by this data."""
    stages = []
    for stage_id, title, action, artifact, kind, decision in STAGES:
        lane = STAGE_LANES[stage_id]
        area = ("requirements" if stage_id in _INTAKE_STAGES else "system_design" if lane == "system"
                else "design_studio" if lane == "design" else "production_review")
        stages.append({
            "id": stage_id, "title": title, "action": action,
            "depends_on": list(DEPENDENCIES[stage_id]),
            "artifact": artifact, "kind": kind,
            "lane": lane, "ui_area": area,
            "human_decision": decision,
            "independent_review": stage_id in {"system_review", "database_review", "backend_review", "release_review"},
            "requires_contracts": ["business", "system", "design"]
                                  if stage_id == "contract_review" or stage_id in _IMPLEMENTATION_STAGES else [],
        })
    by_id = {stage["id"]: stage for stage in stages}
    by_id["wireframe"]["concept"] = "screen_spec"
    by_id["design_candidate"]["input_revisions"] = ["business_contract", "screen_spec", "api_contract"]
    by_id["contract_review"]["require_current_revision_binding"] = True
    return {
        "id": PROFILE_ID,
        "status": "draft_unvalidated",
        "workflow_kind": "dag",
        "workflow_revision": 2,
        "parallel_lanes": ["system", "design"],
        "contracts_are_definitions": True,
        "contracts": _contracts(),
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
