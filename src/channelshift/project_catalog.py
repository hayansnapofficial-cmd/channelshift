"""Pure V1 module plans; selections never grant consent or execution authority.

No network, files, credential reads, provider calls, code execution or approvals.
Source quotations are provenance pointers, not proof of semantic agreement.
All adapter support below describes this implementation, not vendor capability.
"""
from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import re
from datetime import datetime
from urllib.parse import urlsplit

VERSION = "channelshift.project-catalog/v1-draft"
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z", re.ASCII)
_SECRET_REF = re.compile(r"secret-ref:[A-Za-z0-9_-]{1,80}\Z", re.ASCII)
_HASH = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
SEO_CHECKS = ["SEO_RENDERED_HTML", "SEO_URL_SAFETY", "SEO_OG_IMAGE_FETCH",
              "SEO_SITEMAP_ROBOTS", "SEO_REDIRECT_404", "SEO_STRUCTURED_DATA",
              "SEO_SEARCH_CONSOLE_READY"]
DNS_CHECKS = ["DNS_ZONE_SNAPSHOT", "DNS_PLAN_DIFF", "DNS_APPROVAL", "DNS_READBACK",
              "DNS_MAIL_PRESERVED", "DOMAIN_TLS_HTTPS", "DOMAIN_REDIRECT_HEALTH"]
DELIVERY_CHECKS = ["DELIVERY_OWNERSHIP", "DELIVERY_BACKUP_RESTORE", "DELIVERY_SECRET_HANDOFF",
                   "DELIVERY_TEMP_ACCESS_REVOKED", "DELIVERY_CUSTOMER_ACCEPTANCE"]
_GROUPS = {
    "DEVELOPMENT": "GITHUB POSTGRESQL SUPABASE SUPABASE_AUTH STORAGE HOSTING DOMAIN_DNS",
    "SEO": "SEO_BASIC SEARCH_CONSOLE NAVER_SEARCH",
    "ANALYTICS": "GA4 GTM CLARITY",
    "MARKETING": "META_PIXEL GOOGLE_ADS NAVER_ADS KAKAO_PIXEL TIKTOK_PIXEL",
    "LOGIN": "KAKAO_LOGIN NAVER_LOGIN GOOGLE_LOGIN",
    "EXTERNAL_CONTENT": "KAKAO_MAP NAVER_MAP GOOGLE_MAPS",
    "COMMUNICATION": "KAKAO_CHAT CHANNEL_TALK NAVER_TALK EMAIL SMS ALIMTALK",
    "PAYMENT": "PAYMENTS",
    "ADMIN": "SEO_ADMIN INQUIRY_ADMIN PAGE_ADMIN IMAGE_ADMIN POPUP_ADMIN BANNER_ADMIN CONTENT_ADMIN REDIRECT_ADMIN MARKETING_ADMIN ANALYTICS_ADMIN",
}
_PRESETS = {
    "basic": ["SEO_BASIC", "PAGE_ADMIN", "IMAGE_ADMIN", "INQUIRY_ADMIN"],
    "seo": ["SEO_BASIC", "SEO_ADMIN", "SEARCH_CONSOLE", "NAVER_SEARCH", "REDIRECT_ADMIN"],
    "marketing": ["SEO_BASIC", "SEO_ADMIN", "SEARCH_CONSOLE", "NAVER_SEARCH", "GA4", "GTM",
                  "META_PIXEL", "INQUIRY_ADMIN", "MARKETING_ADMIN", "REDIRECT_ADMIN"],
    "consultation": ["SEO_BASIC", "INQUIRY_ADMIN", "KAKAO_CHAT"],
    "content": ["SEO_BASIC", "SEO_ADMIN", "CONTENT_ADMIN", "IMAGE_ADMIN"],
    "custom": ["SEO_BASIC"],
}
_ARTIFACTS = ("requirement", "erd", "migration", "api", "backend", "admin", "frontend",
              "security", "test", "setup", "delivery")
_TRACKING = {"ANALYTICS", "MARKETING"}


def _fail(code="invalid_catalog_input"):
    raise ValueError(code)


def _bounded(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeError):
        _fail()
    if len(raw) > 131072:
        _fail("catalog_input_too_large")


def _shape(value, required, optional=()):
    if type(value) is not dict or not set(required) <= value.keys() <= set(required) | set(optional):
        _fail()


def _text(value, limit=2000):
    if type(value) is not str or not value.strip() or len(value) > limit or any(ord(c) < 32 and c not in "\n\t\r" for c in value):
        _fail()
    return value


def _id(value):
    if type(value) is not str or not _ID.fullmatch(value):
        _fail()
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _sources(items):
    if type(items) is not list or not 1 <= len(items) <= 32:
        _fail("client_source_required")
    result = {}
    for item in items:
        _shape(item, ("id", "text"))
        key = _id(item["id"])
        if key in result:
            _fail()
        result[key] = _text(item["text"], 12000)
    return result


def _references(items, sources):
    if type(items) is not list or len(items) > 16:
        _fail()
    result = []
    for item in items:
        _shape(item, ("source_id", "quote"))
        key, quote = _id(item["source_id"]), _text(item["quote"])
        if key not in sources or quote not in sources[key]:
            _fail("source_quote_mismatch")
        if item in result:
            _fail()
        result.append(dict(item))
    return result


def catalog():
    """Return fresh, versioned specifications, not tested full-stack packages."""
    modules = []
    for group, names in _GROUPS.items():
        for key in names.split():
            category = group if group in _TRACKING | {"EXTERNAL_CONTENT"} else "NECESSARY"
            modules.append({"id": key, "version": 1, "group": group, "consent_category": category,
                            "default_selected": key == "SEO_BASIC", "runtime_enabled": False,
                            "implementation_status": "specification_only", "artifact_specs": list(_ARTIFACTS),
                            "required_checks": SEO_CHECKS[:] if key in {"SEO_BASIC", "SEO_ADMIN"} else ["FUNCTIONAL", "SECURITY", "OWNERSHIP"],
                            "requires_secret_ref": group in {"DEVELOPMENT", "LOGIN", "PAYMENT", "COMMUNICATION"},
                            "dependencies": ["SEO_BASIC"] if key == "SEO_ADMIN" else []})
    return {"format": VERSION, "modules": modules}


def presets():
    """Presets propose modules. Analytics/marketing remain disabled."""
    return copy.deepcopy(_PRESETS)


def build_project_plan(config):
    """Compile source-backed selections into unapproved requirement/task specs.

    Input: project_id, sources[{id,text}], preset, modules[{id,source_refs,
    secret_refs?}], ownership?[{asset_id,owner_ref,environment}], consent_policy?. No raw settings,
    script snippets, credential values, consent booleans or approval flags.
    """
    _bounded(config)
    _shape(config, ("project_id", "sources", "preset", "modules"), ("ownership", "consent_policy"))
    project_id, sources = _id(config["project_id"]), _sources(config["sources"])
    consent = {"mode": "undecided", "selected_by": None, "selected_at": None, "reason": None, "evidence_refs": []}
    if "consent_policy" in config:
        choice = config["consent_policy"]
        _shape(choice, ("mode", "selected_by", "selected_at", "reason", "evidence_refs"))
        if type(choice["mode"]) is not str or choice["mode"] not in {"undecided", "required", "not_required"}:
            _fail("invalid_consent_policy")
        _id(choice["selected_by"])
        _text(choice["reason"])
        try:
            stamp = datetime.fromisoformat(_text(choice["selected_at"], 40).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                _fail("invalid_consent_policy")
        except ValueError:
            _fail("invalid_consent_policy")
        consent = {**choice, "evidence_refs": _references(choice["evidence_refs"], sources)}
    if type(config["preset"]) is not str or config["preset"] not in _PRESETS:
        _fail("unsupported_preset")
    if type(config["modules"]) is not list or len(config["modules"]) > 48:
        _fail()
    specs = {item["id"]: item for item in catalog()["modules"]}
    selected = {key: {"id": key, "source_refs": [], "secret_refs": []} for key in _PRESETS[config["preset"]]}
    explicit = set()
    for item in config["modules"]:
        _shape(item, ("id", "source_refs"), ("secret_refs",))
        key = _id(item["id"])
        if key not in specs:
            _fail("unsupported_module")
        if key in explicit:
            _fail()
        explicit.add(key)
        refs = item.get("secret_refs", [])
        if type(refs) is not list or len(refs) > 8 or any(type(ref) is not str or not _SECRET_REF.fullmatch(ref) for ref in refs) or len(set(refs)) != len(refs):
            _fail("secret_reference_required")
        selected[key] = {"id": key, "source_refs": _references(item["source_refs"], sources), "secret_refs": refs[:]}
    ownership = config.get("ownership", [])
    if type(ownership) is not list or len(ownership) > 32:
        _fail()
    seen_assets = set()
    for item in ownership:
        _shape(item, ("asset_id", "owner_ref", "environment"))
        asset = _id(item["asset_id"])
        _id(item["owner_ref"])
        if type(item["environment"]) is not str or item["environment"] not in {"dev", "staging", "production"} or (asset, item["environment"]) in seen_assets:
            _fail()
        seen_assets.add((asset, item["environment"]))
    requirements, tasks, modules = [], [], []
    for key, selected_item in selected.items():
        spec = specs[key]
        req_id = "REQ-MODULE-" + key
        requirements.append({"id": req_id, "module_id": key, "source_refs": selected_item["source_refs"],
                             "origin": "client_candidate" if selected_item["source_refs"] else "internal_proposal",
                             "customer_confirmation_required": True, "required_scope": False, "status": "planned"})
        modules.append({**selected_item, "version": 1, "status": "planned", "runtime_enabled": False,
                        "consent_category": spec["consent_category"], "consent_status": "not_granted",
                        "provider_mode": "manual", "adapter_status": "not_implemented"})
        previous = None
        for artifact in _ARTIFACTS:
            task_id = key + ":" + artifact
            tasks.append({"id": task_id, "module_id": key, "requirement_id": req_id, "artifact": artifact,
                          "depends_on": [previous] if previous else [], "status": "planned",
                          "required_checks": spec["required_checks"][:] if artifact == "test" else []})
            previous = task_id
    tracking = [item["id"] for item in modules if item["consent_category"] in _TRACKING]
    result = {"format": VERSION, "project_id": project_id, "status": "planned", "approved": False,
              "execution_enabled": False, "source_digests": {key: _digest(text) for key, text in sources.items()},
              "modules": modules, "requirements": requirements, "tasks": tasks,
              "ownership": copy.deepcopy(ownership), "ownership_verified": False,
              "consent_policy": {**consent, "policy_verification_status": "not_verified", "visitor_consent": "not_granted",
                                 "banner_required": None if consent["mode"] == "undecided" else consent["mode"] == "required"},
              "event_contract": {"version": 1, "status": "proposed", "runtime_enabled": False,
                                 "events": [{"id": name, "parameters": ["page_id"], "destinations": tracking[:],
                                             "collects_form_values": False} for name in
                                            ("page_view", "contact_form_start", "contact_form_submit", "phone_click", "kakao_click", "portfolio_view")] if tracking else []},
              "required_decisions": ["customer_scope", "ownership", "consent_policy", "provider_configuration"],
              "delivery_checks": DELIVERY_CHECKS[:]}
    result["plan_digest"] = _digest(result)
    return result


def _domain(value):
    if type(value) is not str or len(value) > 253 or value != value.lower() or value.endswith("."):
        _fail("invalid_domain")
    labels = value.split(".")
    if len(labels) < 2 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label, re.ASCII) for label in labels) or not re.search(r"[a-z]", labels[-1]):
        _fail("invalid_domain")
    if labels[-1] in {"localhost", "local", "internal", "test", "invalid"}:
        _fail("invalid_domain")
    return value


def _dns_record(item, domain, desired=False):
    _shape(item, ("id", "type", "name", "value", "ttl"))
    _id(item["id"])
    if type(item["type"]) is not str or not re.fullmatch(r"[A-Z][A-Z0-9]{0,15}", item["type"]):
        _fail("invalid_dns_record")
    _text(item["name"], 253)
    _zone_name(item["name"], domain)
    _text(item["value"], 4096)
    if type(item["ttl"]) is not int or not 1 <= item["ttl"] <= 604800:
        _fail("invalid_dns_record")
    if desired:
        if item["name"] not in {"@", domain, "www", "www." + domain} or item["type"] not in {"A", "AAAA", "CNAME"}:
            _fail("dns_change_requires_manual_review")
        if item["type"] == "CNAME":
            _domain(item["value"])
        else:
            try:
                address = ipaddress.ip_address(item["value"])
            except ValueError:
                _fail("invalid_dns_record")
            if not address.is_global or address.version != (4 if item["type"] == "A" else 6):
                _fail("invalid_dns_record")
    return copy.deepcopy(item)


def _zone_name(name, domain):
    """Compare relative/FQDN spellings without changing preserved records."""
    if name == "@":
        return domain
    normalized = name.lower().removesuffix(".")
    labels = normalized.split(".")
    if not all((label == "*" and index == 0) or re.fullmatch(r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?", label, re.ASCII)
               for index, label in enumerate(labels)):
        _fail("invalid_dns_record")
    within = normalized == domain or normalized.endswith("." + domain)
    if name.endswith(".") and not within:
        _fail("invalid_dns_record")
    return normalized if within else normalized + "." + domain


def build_dns_change_plan(config):
    """Preserve complete caller snapshot; propose web-only add/modify, never apply.

    Snapshot completeness/provenance and provider readback remain unverified.
    Unsupported providers fail closed. Known providers currently require manual
    work; this is not a claim that their vendors lack an API.
    """
    _bounded(config)
    _shape(config, ("project_id", "sources", "source_refs", "domain", "provider", "snapshot", "desired_records"))
    project_id = _id(config["project_id"])
    sources = _sources(config["sources"])
    refs = _references(config["source_refs"], sources)
    if not refs:
        _fail("client_source_required")
    domain = _domain(config["domain"])
    if type(config["provider"]) is not str or config["provider"] not in {"cloudflare", "gabia", "cafe24", "manual"}:
        _fail("unsupported_provider")
    _shape(config["snapshot"], ("records",))
    records, desired = config["snapshot"]["records"], config["desired_records"]
    if type(records) is not list or not 1 <= len(records) <= 256 or type(desired) is not list or not 1 <= len(desired) <= 32:
        _fail("invalid_dns_snapshot")
    before = {}
    for row in records:
        record = _dns_record(row, domain)
        if record["id"] in before:
            _fail("invalid_dns_snapshot")
        before[record["id"]] = record
    changes, changed = [], set()
    for row in desired:
        record = _dns_record(row, domain, desired=True)
        key = record["id"]
        if key in changed:
            _fail("invalid_dns_record")
        old = before.get(key)
        if old and (old["type"] not in {"A", "AAAA", "CNAME"} or old["name"] != record["name"] or old["type"] != record["type"]):
            _fail("dns_change_requires_manual_review")
        changed.add(key)
        changes.append({"action": "preserve" if old == record else "modify" if old else "add", "before": old, "after": record})
    preserved = [record for key, record in before.items() if key not in changed]
    final_records = preserved + [item["after"] for item in changes]
    for record in final_records:
        if record["type"] == "CNAME" and any(other is not record and _zone_name(other["name"], domain) == _zone_name(record["name"], domain) for other in final_records):
            _fail("dns_cname_conflict")
    result = {"format": "channelshift.dns-plan/v1-draft", "project_id": project_id, "domain": domain,
              "provider": config["provider"], "status": "MANUAL_ACTION_REQUIRED", "approved": False,
              "execution_enabled": False, "source_refs": refs, "snapshot_digest": _digest(config["snapshot"]),
              "snapshot_verified": False, "preserved_records": preserved, "changes": changes,
              "deletions": [], "nameserver_change": "requires_separate_approved_plan",
              "required_checks": DNS_CHECKS[:], "verification_status": "not_invoked",
              "rollback": "requires_provider_specific_restore_plan_and_current_zone_check"}
    result["plan_digest"] = _digest(result)
    return result


def _https(value):
    _text(value, 2048)
    if any(c.isspace() for c in value) or "\\" in value or any(ord(c) < 32 for c in value):
        _fail("unsafe_metadata_url")
    try:
        url = urlsplit(value)
        if url.scheme != "https" or url.username is not None or url.password is not None or url.port not in (None, 443) or url.fragment:
            _fail("unsafe_metadata_url")
        _domain(url.hostname)
    except ValueError:
        _fail("unsafe_metadata_url")
    return value


def resolve_seo_metadata(config):
    """Resolve page > content > site metadata using Storage references only.

    This is URL syntax validation and a candidate; no DNS resolution, image fetch,
    rendered-HTML validation, design approval or crawler visibility is proven.
    """
    _bounded(config)
    _shape(config, ("site", "content", "page", "storage", "design_digest"))
    if type(config["design_digest"]) is not str or not _HASH.fullmatch(config["design_digest"]):
        _fail("design_reference_required")
    keys = {"title", "description", "canonical", "robots", "og_title", "og_description", "og_image_ref",
            "og_url", "og_type", "og_site_name", "twitter_card"}
    metadata = {}
    for layer in ("site", "content", "page"):
        _shape(config[layer], (), keys)
        for key, value in config[layer].items():
            metadata[key] = _text(value)
    for key in ("title", "description", "canonical", "og_image_ref", "og_site_name"):
        if key not in metadata:
            _fail("seo_metadata_required")
    if metadata.get("robots", "index,follow") not in {"index,follow", "noindex,follow", "noindex,nofollow"}:
        _fail()
    if metadata.get("og_type", "website") not in {"website", "article"} or metadata.get("twitter_card", "summary_large_image") not in {"summary", "summary_large_image"}:
        _fail()
    storage = config["storage"]
    if type(storage) is not list or len(storage) > 32:
        _fail()
    images = {}
    for item in storage:
        _shape(item, ("id", "url", "width", "height", "mime"))
        key = _id(item["id"])
        if key in images or any(type(item[dimension]) is not int or not 1 <= item[dimension] <= 10000 for dimension in ("width", "height")):
            _fail()
        if type(item["mime"]) is not str or item["mime"] not in {"image/jpeg", "image/png", "image/webp"}:
            _fail("unsupported_image_type")
        _https(item["url"])
        images[key] = item
    image_ref = _id(metadata["og_image_ref"])
    if image_ref not in images:
        _fail("storage_reference_required")
    image = images[image_ref]
    canonical = _https(metadata["canonical"])
    if metadata.get("og_url", canonical) != canonical:
        _fail("seo_canonical_mismatch")
    result = {"format": "channelshift.seo-candidate/v1-draft", "status": "planned", "approved": False,
              "execution_enabled": False, "design_digest": config["design_digest"], "design_approval_verified": False,
              "title": metadata["title"], "description": metadata["description"], "canonical": canonical,
              "robots": metadata.get("robots", "index,follow"),
              "open_graph": {"og:title": metadata.get("og_title", metadata["title"]),
                             "og:description": metadata.get("og_description", metadata["description"]),
                             "og:url": _https(metadata.get("og_url", canonical)), "og:type": metadata.get("og_type", "website"),
                             "og:site_name": metadata["og_site_name"], "og:image": image["url"]},
              "twitter_card": metadata.get("twitter_card", "summary_large_image"), "image_ref": image_ref,
              "recommended_image_size": {"width": 1200, "height": 630},
              "warnings": [] if (image["width"], image["height"]) == (1200, 630) else ["image_size_recommendation"],
              "verification_status": "not_invoked", "required_checks": SEO_CHECKS[:]}
    result["candidate_digest"] = _digest(result)
    return result
