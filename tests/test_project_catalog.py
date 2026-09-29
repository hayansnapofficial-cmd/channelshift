import copy
import unittest

from channelshift.project_catalog import (
    build_dns_change_plan, build_project_plan, catalog, presets, resolve_seo_metadata,
)


def brief():
    return {"project_id": "PROJECT-1", "sources": [{"id": "SRC-1", "text": "회사 홈페이지와 검색 관리가 필요합니다. 회사 이메일을 유지해주세요."}],
            "preset": "custom", "modules": []}


def dns():
    return {"project_id": "PROJECT-1", "sources": brief()["sources"],
            "source_refs": [{"source_id": "SRC-1", "quote": "회사 이메일을 유지해주세요."}],
            "domain": "example.com", "provider": "cloudflare",
            "snapshot": {"records": [
                {"id": "WEB", "type": "A", "name": "@", "value": "1.1.1.1", "ttl": 300},
                {"id": "MAIL", "type": "MX", "name": "@", "value": "10 mail.example.com", "ttl": 3600},
                {"id": "SPF", "type": "TXT", "name": "@", "value": "v=spf1 -all", "ttl": 3600},
                {"id": "DKIM", "type": "TXT", "name": "selector._domainkey", "value": "v=DKIM1; p=fixture", "ttl": 3600},
                {"id": "DMARC", "type": "TXT", "name": "_dmarc", "value": "v=DMARC1; p=reject", "ttl": 3600},
                {"id": "VERIFY", "type": "TXT", "name": "@", "value": "synthetic-verification-record", "ttl": 3600},
                {"id": "OTHER", "type": "A", "name": "legacy", "value": "1.0.0.1", "ttl": 3600} ]},
            "desired_records": [{"id": "WEB", "type": "A", "name": "@", "value": "8.8.8.8", "ttl": 300},
                                {"id": "WWW", "type": "CNAME", "name": "www", "value": "example.com", "ttl": 300}]}


def seo():
    return {"site": {"title": "회사", "description": "회사 소개", "canonical": "https://example.com/",
                     "og_image_ref": "IMAGE-1", "og_site_name": "회사"},
            "content": {}, "page": {}, "design_digest": "a" * 64,
            "storage": [{"id": "IMAGE-1", "url": "https://cdn.example.com/og.png", "width": 1200, "height": 630, "mime": "image/png"}]}


class ProjectCatalogTests(unittest.TestCase):
    def test_source_required_before_planning(self):
        for sources in ([], None, "원문", [{"id": "SRC-1", "text": " "}]):
            with self.subTest(sources=sources), self.assertRaises(ValueError):
                build_project_plan({**brief(), "sources": sources})

    def test_default_seo_is_proposal_not_client_scope(self):
        result = build_project_plan(brief())
        self.assertEqual(["SEO_BASIC"], [item["id"] for item in result["modules"]])
        self.assertEqual("internal_proposal", result["requirements"][0]["origin"])
        self.assertTrue(result["requirements"][0]["customer_confirmation_required"])
        self.assertFalse(result["requirements"][0]["required_scope"])
        self.assertFalse(result["approved"])
        self.assertFalse(result["execution_enabled"])
        self.assertEqual("undecided", result["consent_policy"]["mode"])

    def test_exact_source_quote_binds_candidate(self):
        config = brief()
        config["modules"] = [{"id": "SEO_ADMIN", "source_refs": [{"source_id": "SRC-1", "quote": "검색 관리가 필요합니다."}]}]
        result = build_project_plan(config)
        self.assertEqual("client_candidate", result["requirements"][1]["origin"])
        self.assertFalse(result["requirements"][1]["required_scope"])
        config["modules"][0]["source_refs"][0]["quote"] = "마케팅에 동의합니다."
        with self.assertRaisesRegex(ValueError, "source_quote_mismatch"):
            build_project_plan(config)

    def test_preset_tracking_never_grants_runtime_or_consent(self):
        result = build_project_plan({**brief(), "preset": "marketing"})
        self.assertIn("GA4", [item["id"] for item in result["modules"]])
        self.assertTrue(all(not item["runtime_enabled"] and item["consent_status"] == "not_granted" for item in result["modules"]))
        self.assertFalse(result["event_contract"]["runtime_enabled"])
        self.assertTrue(all(event["parameters"] == ["page_id"] and not event["collects_form_values"] for event in result["event_contract"]["events"]))

    def test_operator_selects_consent_policy_separately_from_visitor_choice(self):
        for mode, banner in (("undecided", None), ("required", True), ("not_required", False)):
            config = {**brief(), "preset": "marketing", "consent_policy": {
                "mode": mode, "selected_by": "STAFF-1", "selected_at": "2026-09-29T15:00:00+09:00",
                "reason": "담당자가 적용 정책을 검토할 계획", "evidence_refs": []}}
            result = build_project_plan(config)
            with self.subTest(mode=mode):
                self.assertIs(result["consent_policy"]["banner_required"], banner)
                self.assertEqual("not_verified", result["consent_policy"]["policy_verification_status"])
                self.assertEqual("not_granted", result["consent_policy"]["visitor_consent"])
                self.assertFalse(result["event_contract"]["runtime_enabled"])

    def test_consent_needs_decision_provenance_and_timezone(self):
        config = {**brief(), "consent_policy": {"mode": "not_required", "selected_by": "STAFF-1",
                  "selected_at": "2026-09-29", "reason": "검토 예정", "evidence_refs": []}}
        with self.assertRaisesRegex(ValueError, "invalid_consent_policy"):
            build_project_plan(config)
        config["consent_policy"]["selected_at"] = "2026-09-29T06:00:00Z"
        del config["consent_policy"]["reason"]
        with self.assertRaises(ValueError):
            build_project_plan(config)

    def test_secret_values_are_not_configuration(self):
        config = brief()
        config["modules"] = [{"id": "EMAIL", "source_refs": [], "secret_refs": ["secret-ref:EMAIL-PROD"]}]
        self.assertEqual(["secret-ref:EMAIL-PROD"], build_project_plan(config)["modules"][1]["secret_refs"])
        for secret in ("raw-key-value", {"key": "raw-key-value"}, "secret-ref:../../credential"):
            config["modules"][0]["secret_refs"] = [secret]
            with self.subTest(secret=secret), self.assertRaisesRegex(ValueError, "secret_reference_required"):
                build_project_plan(config)

    def test_unknown_and_approval_keys_fail_closed(self):
        for mutation in ({"approved": True}, {"preset": "unknown"}, {"modules": [{"id": "RUN_SHELL", "source_refs": []}]},
                         {"modules": [{"id": "GTM", "source_refs": [], "script": "alert(1)"}]}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                build_project_plan({**brief(), **mutation})

    def test_duplicate_ids_fail_closed(self):
        config = brief()
        config["modules"] = [{"id": "GTM", "source_refs": []}] * 2
        with self.assertRaises(ValueError):
            build_project_plan(config)
        config = brief()
        config["sources"] *= 2
        with self.assertRaises(ValueError):
            build_project_plan(config)

    def test_task_dependencies_reference_earlier_tasks(self):
        result = build_project_plan({**brief(), "preset": "seo"})
        seen = set()
        for task in result["tasks"]:
            self.assertTrue(set(task["depends_on"]) <= seen)
            self.assertEqual("planned", task["status"])
            seen.add(task["id"])
        checks = next(task["required_checks"] for task in result["tasks"] if task["id"] == "SEO_BASIC:test")
        self.assertIn("SEO_RENDERED_HTML", checks)
        self.assertIn("SEO_OG_IMAGE_FETCH", checks)

    def test_outputs_are_fresh_and_inputs_unchanged(self):
        config = brief()
        before = copy.deepcopy(config)
        result = build_project_plan(config)
        self.assertEqual(config, before)
        result["modules"][0]["secret_refs"].append("mutation")
        self.assertEqual([], build_project_plan(config)["modules"][0]["secret_refs"])
        catalog()["modules"].clear()
        presets()["basic"].clear()
        self.assertGreater(len(catalog()["modules"]), 20)
        self.assertIn("SEO_BASIC", presets()["basic"])

    def test_plan_digest_binds_source_and_ownership(self):
        config = brief()
        first = build_project_plan(config)["plan_digest"]
        config["sources"][0]["text"] += " 추가 요구"
        self.assertNotEqual(first, build_project_plan(config)["plan_digest"])
        config["ownership"] = [{"asset_id": "DOMAIN", "owner_ref": "CLIENT-1", "environment": "production"}]
        self.assertFalse(build_project_plan(config)["ownership_verified"])

    def test_size_and_nonfinite_limits(self):
        with self.assertRaises(ValueError):
            build_project_plan({**brief(), "extra": float("nan")})
        with self.assertRaisesRegex(ValueError, "catalog_input_too_large"):
            build_project_plan({**brief(), "extra": "x" * 140000})


class DnsPlanTests(unittest.TestCase):
    def test_web_change_preserves_mail_verification_and_subdomains(self):
        config = dns()
        original = copy.deepcopy(config)
        result = build_dns_change_plan(config)
        self.assertEqual(config, original)
        self.assertEqual({"MAIL", "SPF", "DKIM", "DMARC", "VERIFY", "OTHER"}, {row["id"] for row in result["preserved_records"]})
        self.assertEqual(["modify", "add"], [row["action"] for row in result["changes"]])
        self.assertEqual([], result["deletions"])
        self.assertEqual("MANUAL_ACTION_REQUIRED", result["status"])
        self.assertEqual("not_invoked", result["verification_status"])
        self.assertFalse(result["snapshot_verified"])
        self.assertFalse(result["execution_enabled"])

    def test_mail_record_cannot_be_repurposed_or_deleted(self):
        config = dns()
        config["desired_records"][0]["id"] = "MAIL"
        with self.assertRaisesRegex(ValueError, "dns_change_requires_manual_review"):
            build_dns_change_plan(config)
        config = dns()
        config["deletions"] = ["MAIL"]
        with self.assertRaises(ValueError):
            build_dns_change_plan(config)

    def test_cname_cannot_replace_mx_apex_or_coexist(self):
        config = dns()
        config["desired_records"] = [{"id": "NEW", "type": "CNAME", "name": "example.com", "value": "host.example.com", "ttl": 300}]
        with self.assertRaisesRegex(ValueError, "dns_cname_conflict"):
            build_dns_change_plan(config)

    def test_unknown_provider_and_empty_snapshot_block(self):
        config = dns()
        config["provider"] = "unimplemented_vendor"
        with self.assertRaisesRegex(ValueError, "unsupported_provider"):
            build_dns_change_plan(config)
        config = dns()
        config["snapshot"]["records"] = []
        with self.assertRaisesRegex(ValueError, "invalid_dns_snapshot"):
            build_dns_change_plan(config)

    def test_cname_conflict_uses_normalized_dns_names(self):
        config = dns()
        config["snapshot"]["records"].append({"id": "OLDWWW", "type": "A", "name": "WWW.EXAMPLE.COM.", "value": "1.1.1.1", "ttl": 300})
        with self.assertRaisesRegex(ValueError, "dns_cname_conflict"):
            build_dns_change_plan(config)

    def test_nameserver_and_private_ip_changes_require_other_workflow(self):
        for kind, value in (("NS", "ns.example.com"), ("A", "127.0.0.1"), ("AAAA", "8.8.8.8")):
            config = dns()
            config["desired_records"][0].update(type=kind, value=value)
            with self.subTest(kind=kind, value=value), self.assertRaises(ValueError):
                build_dns_change_plan(config)

    def test_snapshot_and_target_change_digest(self):
        config = dns()
        first = build_dns_change_plan(config)
        config["snapshot"]["records"][1]["ttl"] = 600
        second = build_dns_change_plan(config)
        self.assertNotEqual(first["snapshot_digest"], second["snapshot_digest"])
        self.assertNotEqual(first["plan_digest"], second["plan_digest"])


class SeoCandidateTests(unittest.TestCase):
    def test_page_content_site_precedence_and_six_og_fields(self):
        config = seo()
        config["content"] = {"title": "콘텐츠 제목", "og_description": "콘텐츠 설명"}
        config["page"] = {"title": "페이지 제목", "canonical": "https://example.com/about"}
        result = resolve_seo_metadata(config)
        self.assertEqual("페이지 제목", result["open_graph"]["og:title"])
        self.assertEqual("콘텐츠 설명", result["open_graph"]["og:description"])
        self.assertEqual("https://example.com/about", result["open_graph"]["og:url"])
        self.assertEqual(6, len(result["open_graph"]))
        self.assertEqual("summary_large_image", result["twitter_card"])
        self.assertEqual("not_invoked", result["verification_status"])
        self.assertFalse(result["design_approval_verified"])

    def test_storage_reference_and_design_binding_required(self):
        config = seo()
        config["page"] = {"og_image_ref": "MISSING"}
        with self.assertRaisesRegex(ValueError, "storage_reference_required"):
            resolve_seo_metadata(config)
        config = seo()
        config["design_digest"] = "approved"
        with self.assertRaisesRegex(ValueError, "design_reference_required"):
            resolve_seo_metadata(config)

    def test_metadata_url_syntax_blocks_unsafe_destinations(self):
        urls = ["http://example.com", "javascript:alert(1)", "https://127.0.0.1/", "https://[::1]/",
                "https://localhost/", "https://service.internal/", "https://user:pass@example.com/",
                "https://example.com:8080/", "https://example.com/#token", "https://example.com/\nheader"]
        for url in urls:
            config = seo()
            config["site"]["canonical"] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                resolve_seo_metadata(config)

    def test_image_url_and_mime_are_validated_without_fetch(self):
        config = seo()
        config["storage"][0]["url"] = "https://169.254.169.254/latest/meta-data"
        with self.assertRaises(ValueError):
            resolve_seo_metadata(config)
        config = seo()
        config["storage"][0]["mime"] = "image/svg+xml"
        with self.assertRaisesRegex(ValueError, "unsupported_image_type"):
            resolve_seo_metadata(config)

    def test_dimensions_are_recommendations_not_platform_guarantees(self):
        config = seo()
        config["storage"][0].update(width=600, height=600)
        result = resolve_seo_metadata(config)
        self.assertEqual(["image_size_recommendation"], result["warnings"])
        self.assertEqual("planned", result["status"])
        self.assertFalse(result["approved"])

    def test_canonical_mismatch_and_invalid_robots_are_rejected(self):
        config = seo()
        config["page"] = {"og_url": "https://another.example.com/"}
        with self.assertRaisesRegex(ValueError, "seo_canonical_mismatch"):
            resolve_seo_metadata(config)
        config = seo()
        config["page"] = {"robots": "index,ignore-all-security"}
        with self.assertRaises(ValueError):
            resolve_seo_metadata(config)


if __name__ == "__main__":
    unittest.main()
