"""Source-first planning with two design lanes and required security gates."""
import unittest

from channelshift.delivery_profile import DEPENDENCIES, INPUTS, STAGES, delivery_plan, standard_site_profile


class DeliveryProfileTests(unittest.TestCase):
    def test_profile_is_an_acyclic_graph_with_intake_as_its_only_root(self):
        profile = standard_site_profile()
        stages = {stage['id']: stage for stage in profile['stages']}
        self.assertEqual(profile['workflow_kind'], 'dag')
        self.assertEqual(set(stages), set(DEPENDENCIES))
        self.assertEqual(len(stages), len(profile['stages']))
        self.assertEqual([stage['id'] for stage in stages.values() if not stage['depends_on']], ['intake'])
        active, visited = set(), set()

        def visit(stage_id):
            self.assertNotIn(stage_id, active, 'Dependency cycle')
            if stage_id in visited:
                return
            active.add(stage_id)
            for dependency in stages[stage_id]['depends_on']:
                self.assertIn(dependency, stages)
                visit(dependency)
            active.remove(stage_id)
            visited.add(stage_id)

        for stage_id in stages:
            visit(stage_id)
        self.assertEqual(visited, set(stages))

    def test_source_business_review_and_domain_model_precede_system_design(self):
        stages = {stage['id']: stage for stage in standard_site_profile()['stages']}
        chain = ['intake', 'environment_check', 'requirements', 'business_review', 'domain_model',
                 'erd', 'database_schema', 'api_contract', 'backend_architecture', 'system_review']
        for previous, current in zip(chain, chain[1:]):
            self.assertIn(previous, stages[current]['depends_on'])
        self.assertEqual(stages['business_review']['human_decision'], 'business')
        self.assertTrue(stages['system_review']['independent_review'])
        self.assertEqual(stages['system_review']['human_decision'], 'risk_based')

    def test_screen_spec_runs_alongside_system_design_but_candidate_binds_api(self):
        profile = standard_site_profile()
        stages = {stage['id']: stage for stage in profile['stages']}
        self.assertEqual(profile['parallel_lanes'], ['system', 'design'])
        self.assertEqual(stages['wireframe']['depends_on'], ['business_review'])
        self.assertEqual(stages['wireframe']['concept'], 'screen_spec')
        self.assertEqual(stages['wireframe']['artifact'], 'screen_spec')
        self.assertEqual(stages['wireframe']['lane'], 'design')
        self.assertEqual(stages['erd']['lane'], 'system')
        self.assertEqual(stages['design_candidate']['depends_on'], ['wireframe', 'api_contract'])
        self.assertEqual(stages['design_candidate']['input_revisions'], ['business_contract', 'screen_spec', 'api_contract'])
        self.assertEqual(stages['design_preview']['depends_on'], ['design_candidate'])
        self.assertEqual(stages['design_review']['depends_on'], ['design_preview'])
        self.assertEqual(stages['design_review']['human_decision'], 'design')
        self.assertNotIn('system_review', stages['design_review']['depends_on'])
        self.assertNotIn('design_review', stages['erd']['depends_on'])

    def test_implementation_waits_for_both_lanes_and_three_current_contracts(self):
        profile = standard_site_profile()
        stages = {stage['id']: stage for stage in profile['stages']}
        gate = stages['contract_review']
        self.assertEqual(gate['depends_on'], ['system_review', 'design_review'])
        self.assertEqual(gate['requires_contracts'], ['business', 'system', 'design'])
        self.assertTrue(gate['require_current_revision_binding'])
        self.assertEqual(gate['human_decision'], 'none')
        self.assertFalse(gate['independent_review'])
        chain = ['contract_review', 'database_build', 'database_review', 'backend_build', 'backend_review',
                 'frontend_build', 'acceptance', 'release_review', 'deployment_approval', 'deployment', 'handover']
        for previous, current in zip(chain, chain[1:]):
            self.assertIn(previous, stages[current]['depends_on'])
            self.assertEqual(stages[current]['requires_contracts'], ['business', 'system', 'design'])
        self.assertEqual(stages['database_review']['human_decision'], 'risk_based')
        self.assertEqual(stages['deployment_approval']['human_decision'], 'deployment')
        self.assertEqual(stages['handover']['human_decision'], 'customer_acceptance')

    def test_contracts_define_versioned_records_without_creating_approvals(self):
        profile = standard_site_profile()
        stages = {stage['id']: stage for stage in profile['stages']}
        self.assertTrue(profile['contracts_are_definitions'])
        self.assertEqual(set(profile['contracts']), {'business', 'system', 'design'})
        for contract in profile['contracts'].values():
            self.assertTrue(contract['components'])
            self.assertTrue(contract['required_binding'])
            self.assertEqual(stages[contract['review_stage']]['artifact'], contract['artifact'])
            self.assertNotIn('approved', contract)
            self.assertNotIn('approved_by', contract)
        design = profile['contracts']['design']
        self.assertEqual(design['implementation_requires'], 'approved_revision')
        self.assertIn('system_contract_revision', design['required_binding'])
        self.assertIn('candidate_revision', design['required_binding'])
        self.assertIn('preview_build_digest', design['required_binding'])

    def test_four_ui_areas_cover_stages_and_keep_legacy_intervention_ids(self):
        profile = standard_site_profile()
        areas = {area['id']: area for area in profile['ui_areas']}
        self.assertEqual(set(areas), {'requirements', 'system_design', 'design_studio', 'production_review'})
        assigned = [stage_id for area in areas.values() for stage_id in area['stage_ids']]
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertEqual(set(assigned), {stage['id'] for stage in profile['stages']})
        self.assertEqual(set(areas['design_studio']['stage_ids']),
                         {'wireframe', 'design_candidate', 'design_preview', 'design_review'})
        legacy = {'intake', 'environment_check', 'requirements', 'wireframe', 'design_review', 'erd',
                  'api_contract', 'contract_review', 'database_build', 'database_review', 'backend_build',
                  'backend_review', 'frontend_build', 'acceptance', 'release_review', 'deployment_approval',
                  'deployment', 'handover'}
        self.assertTrue(legacy <= set(assigned))
        self.assertTrue(all(type(stage) is tuple and len(stage) == 6 for stage in STAGES))

    def test_missing_intake_is_guidance_not_approval(self):
        plan = delivery_plan()
        self.assertEqual(len(plan['missing_inputs']), 11)
        self.assertFalse(plan['source_present'])
        self.assertEqual(set(plan['blocked_stages']), {stage[0] for stage in STAGES} - {'intake'})
        self.assertFalse(plan['runtime_connected'])
        self.assertEqual(plan['mode'], 'planning_only')
        self.assertEqual(plan['profile']['stack']['status'], 'unselected')

    def test_filled_intake_stays_unexecuted_and_independent(self):
        brief = {'format': 'channelshift.delivery-brief/v1', 'project_name': 'Synthetic',
                 'goal': 'Make a contact site', 'client_request': 'Make a contact site',
                 'inputs': {key: 'Synthetic supplied value' for key in INPUTS}}
        plan = delivery_plan(brief)
        self.assertEqual(plan['missing_inputs'], [])
        self.assertFalse(plan['runtime_connected'])
        plan['brief']['inputs']['admin_access'] = 'changed'
        plan['profile']['stages'].clear()
        self.assertEqual(brief['inputs']['admin_access'], 'Synthetic supplied value')
        self.assertEqual(len(standard_site_profile()['stages']), len(STAGES))
        fresh = standard_site_profile()
        fresh['contracts']['design']['required_binding'].clear()
        fresh['ui_areas'][0]['stage_ids'].clear()
        fresh['stages'][0]['depends_on'].append('untrusted')
        other = standard_site_profile()
        self.assertTrue(other['contracts']['design']['required_binding'])
        self.assertTrue(other['ui_areas'][0]['stage_ids'])
        self.assertEqual(other['stages'][0]['depends_on'], [])

    def test_natural_language_is_preserved_without_fabricated_extraction(self):
        request = '회사 소개와 문의 폼을 만들어 주세요. 문의는 관리자만 보게 해 주세요.'
        plan = delivery_plan({'format': 'channelshift.delivery-brief/v1', 'project_name': 'Demo',
                              'goal': '', 'inputs': {}, 'client_request': request})
        self.assertEqual(plan['brief']['client_request'], request)
        self.assertEqual(plan['natural_language_processing'], 'not_invoked')
        self.assertTrue(plan['missing_inputs'])
        binding = plan['profile']['execution_binding']
        self.assertEqual(binding['account'], 'operator_owned')
        self.assertEqual(binding['auth'], 'chatgpt_managed')
        self.assertFalse(binding['api_key_fallback'])
        self.assertEqual(binding['status'], 'not_connected')

    def test_untrusted_flags_types_and_unknown_inputs_are_rejected(self):
        brief = {'format': 'channelshift.delivery-brief/v1', 'project_name': 'Demo', 'goal': '', 'inputs': {}}
        for key, value in [('approved', True), ('shell', 'whoami')]:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'invalid_delivery_brief'):
                delivery_plan(dict(brief, **{key: value}))
        for value in ([], {'unknown': 'value'}, {'admin_access': True}, {'admin_access': 'x'*4001}):
            with self.subTest(value=type(value).__name__), self.assertRaises(ValueError):
                delivery_plan(dict(brief, inputs=value))
        for value in (True, 'x'*201, '\ud800'):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                delivery_plan(dict(brief, project_name=value))

    def test_security_gates_are_required_ancestors_of_build_acceptance_and_deployment(self):
        stages = {stage['id']: stage for stage in standard_site_profile()['stages']}

        def ancestors(stage_id):
            result = set()
            pending = list(stages[stage_id]['depends_on'])
            while pending:
                current = pending.pop()
                if current not in result:
                    result.add(current)
                    pending.extend(stages[current]['depends_on'])
            return result

        for stage_id in ('database_build', 'backend_build', 'frontend_build'):
            self.assertTrue({'intake', 'business_review', 'system_review', 'design_review',
                             'security_requirements', 'threat_model', 'authorization_policy',
                             'security_design_review'} <= ancestors(stage_id))
        self.assertIn('database_policy_tests', ancestors('database_review'))
        self.assertIn('backend_security_tests', ancestors('frontend_build'))
        self.assertIn('frontend_build', ancestors('security_implementation_review'))
        self.assertIn('security_implementation_review', ancestors('acceptance'))
        for stage_id in ('release_review', 'deployment_approval', 'deployment', 'handover'):
            self.assertTrue({'security_design_review', 'security_implementation_review',
                             'security_release_review'} <= ancestors(stage_id))
        self.assertIn('operations_security', ancestors('handover'))
        # Security must not turn the two design lanes back into a linear flow.
        self.assertNotIn('design_review', ancestors('erd'))
        self.assertNotIn('system_review', ancestors('wireframe'))

    def test_security_contract_preserves_three_contracts_and_source_provenance(self):
        profile = standard_site_profile()
        security = profile['security_contract']
        self.assertEqual(set(profile['contracts']), {'business', 'system', 'design'})
        self.assertEqual(profile['cross_cutting_lanes'], ['security'])
        self.assertEqual(set(security['covers_contracts']), set(profile['contracts']))
        self.assertTrue(security['cross_cutting'])
        self.assertIn('source_digest', security['required_binding'])
        self.assertIn('authorization_policy_revision', security['required_binding'])
        self.assertIn('provider_capability_digest', security['required_binding'])
        self.assertEqual(set(security['requirement_origins']), {'client_source', 'internal_security_baseline'})
        self.assertIn('exact_quote', security['client_origin_requires'])
        self.assertIn('baseline_reference', security['internal_origin_requires'])
        gates = [stage for stage in profile['stages'] if stage.get('gate_id')]
        self.assertEqual({stage['gate_id'] for stage in gates}, {'SG1', 'SG2', 'SG3'})
        self.assertTrue(all(stage['independent_review'] and stage['require_current_revision_binding'] for stage in gates))

    def test_plan_never_claims_provider_or_security_execution(self):
        for brief in (None, {'format': 'channelshift.delivery-brief/v1', 'project_name': 'Synthetic',
                             'goal': 'Contact site', 'client_request': 'Contact site',
                             'inputs': {key: 'Synthetic value' for key in INPUTS}}):
            profile = delivery_plan(brief)['profile']
            self.assertFalse(profile['security_execution']['runtime_connected'])
            self.assertFalse(profile['security_contract']['enforcement_connected'])
            self.assertEqual(profile['security_execution']['checks'], 'not_invoked')
            self.assertEqual(profile['security_execution']['policy_compilation'], 'not_invoked')
            self.assertEqual(profile['security_execution']['isolated_database_tests'], 'not_invoked')
            self.assertTrue(all(stage['execution_status'] == 'not_invoked' for stage in profile['stages']))
            providers = profile['provider_contracts']
            self.assertTrue(providers['authorization']['provider_neutral'])
            self.assertEqual(providers['authorization']['capability_mismatch'], 'HOLD')
            self.assertEqual(providers['authorization']['unknown_claim_provenance'], 'HOLD')
            self.assertFalse(providers['authorization']['simulation_is_evidence'])
            self.assertEqual(providers['database']['status'], 'not_connected')
            self.assertFalse(providers['database']['capabilities_verified'])
            self.assertEqual(providers['source']['status'], 'not_connected')
            self.assertTrue(providers['source']['same_commit_required'])
            self.assertEqual(providers['source']['unknown_or_missing_check'], 'HOLD')
            self.assertFalse(providers['source']['webhook_receiver_implemented'])

    def test_new_security_stages_require_source_and_profiles_do_not_share_mutable_state(self):
        plan = delivery_plan()
        profile = plan['profile']
        security_ids = {stage['id'] for stage in profile['stages'] if stage['lane'] == 'security'}
        self.assertTrue(security_ids <= set(plan['blocked_stages']))
        self.assertTrue(all(stage['requires_original_source'] for stage in profile['stages']
                            if stage['id'] in security_ids))
        profile['security_contract']['required_binding'].clear()
        profile['provider_contracts']['database']['required_evidence'].clear()
        profile['security_contract']['gate_ids'].clear()
        next(stage for stage in profile['stages'] if stage['id'] == 'database_policy_tests')['required_evidence'].clear()
        fresh = standard_site_profile()
        self.assertTrue(fresh['security_contract']['required_binding'])
        self.assertTrue(fresh['provider_contracts']['database']['required_evidence'])
        self.assertEqual(len(fresh['security_contract']['gate_ids']), 3)
        self.assertIn('admin_and_bypass_roles', next(stage for stage in fresh['stages']
                                                    if stage['id'] == 'database_policy_tests')['required_evidence'])

    def test_delivery_requires_domain_seo_and_handover_evidence_without_claiming_completion(self):
        stages = {stage['id']: stage for stage in standard_site_profile()['stages']}
        expected = {
            'acceptance': {'SEO_RENDERED_HTML', 'SEO_URL_SAFETY', 'SEO_OG_IMAGE_FETCH',
                           'SEO_SITEMAP_ROBOTS', 'SEO_REDIRECT_404', 'SEO_STRUCTURED_DATA'},
            'deployment': {'DNS_ZONE_SNAPSHOT', 'DNS_PLAN_DIFF', 'DNS_APPROVAL', 'DNS_READBACK',
                           'DNS_MAIL_PRESERVED', 'DOMAIN_TLS_HTTPS', 'DOMAIN_REDIRECT_HEALTH'},
            'handover': {'SEO_SEARCH_CONSOLE_READY', 'DELIVERY_OWNERSHIP', 'DELIVERY_BACKUP_RESTORE',
                         'DELIVERY_SECRET_HANDOFF', 'DELIVERY_TEMP_ACCESS_REVOKED',
                         'DELIVERY_CUSTOMER_ACCEPTANCE'},
        }
        for stage_id, evidence in expected.items():
            self.assertEqual(set(stages[stage_id]['required_evidence']), evidence)
            self.assertEqual(stages[stage_id]['evidence_status'], 'not_invoked')
            self.assertEqual(stages[stage_id]['applicability_requires'], 'recorded_contract_decision')
            self.assertIn('docs/INFRASTRUCTURE_DELIVERY.md', stages[stage_id]['evidence_contracts'])
            self.assertNotIn('approved', stages[stage_id])


if __name__ == '__main__':
    unittest.main()
