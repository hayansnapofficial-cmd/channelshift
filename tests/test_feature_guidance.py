"""Real durable choice snapshots with synthetic extraction and no paid calls."""
import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from channelshift import feature_guidance
from channelshift.delivery_workspace import DeliveryWorkspace, _extraction_snapshot
from channelshift.pipeline_workspace import PipelineWorkspace
from test_pipeline_workspace import SOURCE, candidate, erd, generated


def advice_result(stage, context):
    from channelshift.feature_advisor import FORMAT, input_digest
    kinds = {'features': ('requested', 'conflicts'), 'reference': ('relevant', 'supported'),
             'artifacts': ('aligned', 'unsupported_claim')}[stage]
    return {'format': FORMAT, 'stage': stage, 'advisory_only': True, 'approval_granted': False,
            'input_digest': input_digest(stage, context), 'model': 'jev-synthetic',
            'judgments': [{'id': item['id'] + '.' + kind, 'item_id': item['id'], 'kind': kind,
                           'probability': .9, 'uncertain': not item['evidence_complete']}
                          for item in context['items'] for kind in kinds],
            'usage': {'input_tokens': 0, 'output_tokens': 0}}


class FeatureGuidanceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='channelshift-features-test-')
        self.addCleanup(folder.cleanup)
        self.extract = Mock(side_effect=candidate)
        self.delivery = DeliveryWorkspace(Path(folder.name) / 'delivery.sqlite3', extract=self.extract,
                                          generate_erd=erd)
        self.pipeline = PipelineWorkspace(self.delivery, generate=generated)
        self.addCleanup(self.delivery.close)
        self.addCleanup(self.pipeline.close)
        self.view = self.pipeline.create('기능 선택 합성 프로젝트', SOURCE, 'service')
        self.project_id = self.view['project']['id']

    def refresh(self):
        self.view = self.pipeline.get(self.project_id)
        return self.view

    def act(self, action, **payload):
        self.view = self.pipeline.action(self.project_id, self.view['pipeline']['revision'], action, payload)
        return self.view

    def drain(self):
        self.delivery._jobs.submit(lambda: None).result(5)
        self.pipeline._jobs.submit(lambda: None).result(10)
        return self.refresh()

    def selections(self):
        return [{'id': card['id'], 'option': card['options'][0]['id'], 'note': ''}
                for card in self.view['pipeline']['guidance']['cards']]

    def ready(self):
        self.act('save_features', decisions=self.selections())
        self.act('analyze', answers=[])
        self.drain()
        self.act('confirm')

    def test_default_has_no_preselection_and_complete_decisions_flow_into_review_and_erd(self):
        guidance = self.view['pipeline']['guidance']
        self.assertTrue(guidance['enabled'])
        self.assertEqual(len(guidance['cards']), 8)
        self.assertEqual(len(guidance['unresolved']), 8)
        self.assertTrue(all(card['selected'] is None and card['note'] == '' for card in guidance['cards']))
        self.assertNotIn('none', [x['id'] for x in guidance['cards'][-1]['options']])
        self.ready()
        self.assertTrue(self.view['pipeline']['confirmed'])
        review = self.view['project']['requirements_review']
        self.assertEqual(len(review['feature_decisions']['decisions']), 8)
        self.assertEqual(review['production_guides'], self.view['project']['production_guides'])
        self.assertIn('기능 선택 · 작업자 입력', self.extract.call_args.args[0])
        self.act('generate', stage='wireframe')
        self.drain()
        self.act('approve', stage='wireframe', note='대표 화면 확인')
        self.act('generate', stage='erd')
        self.drain()
        self.assertEqual(next(s for s in self.view['pipeline']['stages'] if s['id'] == 'erd')['state'], 'generated')

    def test_unknown_deferred_and_empty_can_be_saved_and_analyzed_but_never_confirmed(self):
        rows = self.selections()
        for option in (None, 'unsure', 'later'):
            with self.subTest(option=option):
                rows[1] = {'id': 'booking', 'option': option, 'note': '담당자가 확인한 다음 예약을 확정하고 싶어요.'}
                self.act('save_features', decisions=rows)
                self.act('analyze', answers=[])
                self.drain()
                self.assertTrue(self.view['pipeline']['analysis_current'])
                self.assertEqual(self.view['pipeline']['guidance']['unresolved'], ['booking'])
                with self.assertRaisesRegex(ValueError, '^feature_guidance_required$'):
                    self.act('confirm')
                with self.assertRaisesRegex(ValueError, '^feature_guidance_required$'):
                    self.delivery.advance_to_review(self.project_id, self.view['project']['intervention_revision'])
                self.assertIn('담당자가 확인한 다음', self.extract.call_args.args[0])

    def test_invalid_batch_has_no_partial_write_and_stale_revisions_do_not_overwrite(self):
        initial = copy.deepcopy(self.view)
        valid = {'id': 'booking', 'option': 'approval', 'note': '담당자가 확정'}
        invalid = [
            dict(valid, id='unknown'), dict(valid, option='invented'), dict(valid, note='x' * 1001),
            dict(valid, note='bad\ud800'), dict(valid, note='bad\x00'), dict(valid, extra=True),
            {'id': 'seo', 'option': 'none', 'note': ''}, dict(valid, option=[])]
        for item in invalid:
            with self.subTest(item=str(item)[:40]):
                with self.assertRaisesRegex(ValueError, '^invalid_feature_decisions$'):
                    self.act('save_features', decisions=[self.selections()[0], item])
                self.assertEqual(self.refresh(), initial)
        with self.assertRaisesRegex(ValueError, '^invalid_feature_decisions$'):
            self.act('save_features', decisions=[valid, valid])
        self.act('save_features', decisions=[valid])
        with self.assertRaisesRegex(ValueError, '^pipeline_revision_conflict$'):
            self.pipeline.action(self.project_id, initial['pipeline']['revision'], 'save_features', {'decisions': self.selections()})
        with self.assertRaisesRegex(ValueError, '^delivery_revision_conflict$'):
            self.delivery.save_features(self.project_id, [dict(valid, option='none')], initial['project']['intervention_revision'])
        self.assertEqual(self.refresh()['pipeline']['guidance']['cards'][1]['selected'], 'approval')

    def test_current_exclusion_supersedes_previous_option_and_notes_history_remains(self):
        self.act('save_features', decisions=[{'id': 'booking', 'option': 'instant', 'note': '이전 예약 설명'}])
        self.act('save_features', decisions=[{'id': 'booking', 'option': 'none', 'note': '현재는 문의만 받겠습니다.'}])
        snapshot = _extraction_snapshot(self.view['project'])
        self.assertIn('예약 기능 없음', snapshot['text'])
        self.assertIn('현재는 문의만 받겠습니다.', snapshot['text'])
        self.assertNotIn('이전 예약 설명', snapshot['text'])
        self.assertNotIn('날짜 선택·즉시 확정', snapshot['text'])
        history = self.view['project']['feature_history']
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]['decisions'][1]['option'], 'instant')
        event = next(row for row in self.view['pipeline']['history'] if row['kind'] == 'feature_decisions_saved')
        self.assertIn('현재는 문의만 받겠습니다.', event['note'])
        self.assertEqual(self.view['project']['source']['text'], SOURCE)

    def test_change_invalidates_confirmed_and_downstream_but_noop_does_not(self):
        self.ready()
        self.act('generate', stage='wireframe')
        self.drain()
        self.act('approve', stage='wireframe', note='확인')
        previous = copy.deepcopy(self.view)
        self.act('save_features', decisions=self.selections())
        self.assertEqual(self.view, previous)
        self.act('save_features', decisions=[{'id': 'design', 'option': 'custom', 'note': '브랜드 사진을 중심으로 구성'}])
        self.assertFalse(self.view['pipeline']['confirmed'])
        self.assertFalse(self.view['pipeline']['analysis_current'])
        self.assertEqual(next(s for s in self.view['pipeline']['stages'] if s['id'] == 'wireframe')['state'], 'stale')
        with self.assertRaisesRegex(ValueError, '^pipeline_analysis_required$'):
            self.act('confirm')

    def test_reopen_persists_catalog_and_guides_without_adopting_current_defaults(self):
        self.ready()
        previous = copy.deepcopy(self.view)
        changed = feature_guidance.catalog()
        changed['cards'][0]['title'] = '새 버전에서 바뀐 제목'
        with patch('channelshift.feature_guidance.catalog', return_value=changed):
            reopened_delivery = DeliveryWorkspace(self.delivery.path)
            reopened_pipeline = PipelineWorkspace(reopened_delivery, self.pipeline.path)
            try:
                self.assertEqual(reopened_pipeline.get(self.project_id), previous)
            finally:
                reopened_pipeline.close()
                reopened_delivery.close()

    def test_legacy_project_stays_unguided_and_revision_is_not_changed_by_read(self):
        legacy = self.pipeline.create('기존 흐름', SOURCE, 'service', guided=False)
        self.assertFalse(legacy['pipeline']['guidance']['enabled'])
        self.assertIsNone(legacy['pipeline']['production_guides'])
        self.assertEqual(self.pipeline.get(legacy['project']['id']), legacy)
        self.assertNotIn('feature_revision', _extraction_snapshot(legacy['project']))
        with self.assertRaisesRegex(ValueError, '^feature_guidance_unavailable$'):
            self.pipeline.action(legacy['project']['id'], legacy['pipeline']['revision'], 'save_features',
                                 {'decisions': [{'id': 'booking', 'option': 'none', 'note': ''}]})

    def test_busy_extraction_does_not_allow_choice_writes_and_freezes_input(self):
        self.act('save_features', decisions=self.selections())
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = _extraction_snapshot(self.view['project'])
        def extract(text):
            entered.set()
            release.wait(5)
            return candidate(text)
        self.extract.side_effect = extract
        self.act('analyze', answers=[])
        self.assertTrue(entered.wait(3))
        with self.assertRaisesRegex(ValueError, '^pipeline_busy$'):
            self.act('save_features', decisions=[{'id': 'booking', 'option': 'none', 'note': ''}])
        release.set()
        self.drain()
        self.assertEqual(self.view['project']['candidate_input'], original)

    def test_all_aggregate_input_is_bounded_before_choice_commit(self):
        before = copy.deepcopy(self.view)
        rows = [dict(row, note='한' * 1000) for row in self.selections()]
        # Client request + all notes + catalog wording cannot overflow extraction.
        self.delivery.add_request(self.project_id, '가' * 3500, self.view['project']['intervention_revision'])
        self.refresh()
        before = copy.deepcopy(self.view)
        with self.assertRaisesRegex(ValueError, '^delivery_input_limit$'):
            self.act('save_features', decisions=rows)
        self.assertEqual(self.refresh(), before)

    def test_model_recommendations_are_advice_not_selections_or_confirmation(self):
        self.extract.side_effect = lambda text: dict(candidate(text), feature_recommendations=[{
            'card_id': 'booking', 'option_id': 'approval', 'reason': '담당자 확인 요구에 맞습니다.',
            'tradeoff': '담당자가 예약 요청을 확인해야 합니다.'}])
        self.act('save_features', decisions=[{'id': 'booking', 'option': 'unsure', 'note': '확인 후 확정하고 싶어요.'}])
        self.act('analyze', answers=[])
        self.drain()
        self.assertEqual(self.view['project']['candidate']['feature_recommendations'][0]['option_id'], 'approval')
        self.assertEqual(self.view['pipeline']['guidance']['cards'][1]['selected'], 'unsure')
        with self.assertRaisesRegex(ValueError, '^feature_guidance_required$'):
            self.act('confirm')

    def test_recommendations_cannot_target_excluded_deferred_unanswered_or_selected_cards(self):
        recommendation = {'card_id': 'booking', 'option_id': 'approval', 'reason': '확인 후 확정',
                          'tradeoff': '담당자 확인이 필요합니다.'}
        for option in (None, 'none', 'later', 'request', 'instant', 'approval'):
            with self.subTest(option=option):
                self.extract.side_effect = candidate
                self.act('save_features', decisions=[{'id': 'booking', 'option': option, 'note': ''}])
                self.act('analyze', answers=[])
                self.drain()
                previous = copy.deepcopy(self.view['project']['candidate'])
                recorded = sum(row['kind'] == 'candidate_recorded' for row in self.view['project']['events'])
                self.extract.side_effect = lambda text: dict(candidate(text), feature_recommendations=[recommendation])
                self.act('analyze', answers=[])
                self.drain()
                self.assertEqual(self.view['project']['state'], 'NEEDS_ATTENTION')
                self.assertEqual(self.view['project']['events'][-1]['payload']['code'], 'codex_invalid_output')
                self.assertEqual(self.view['project']['candidate'], previous)
                self.assertEqual(sum(row['kind'] == 'candidate_recorded' for row in self.view['project']['events']), recorded)
                self.assertEqual(self.view['pipeline']['guidance']['cards'][1]['selected'], option)

    def test_one_invalid_recommendation_rejects_the_entire_new_candidate(self):
        self.act('save_features', decisions=[{'id': 'booking', 'option': 'unsure', 'note': ''},
                                            {'id': 'payment', 'option': 'none', 'note': '결제 제외'}])
        self.act('analyze', answers=[])
        self.drain()
        previous = copy.deepcopy(self.view['project']['candidate'])
        self.extract.side_effect = lambda text: dict(candidate(text), feature_recommendations=[
            {'card_id': 'booking', 'option_id': 'approval', 'reason': '담당자 확인', 'tradeoff': '확인 작업 필요'},
            {'card_id': 'payment', 'option_id': 'one_time', 'reason': '단건 결제', 'tradeoff': '결제 계약 필요'}])
        self.act('analyze', answers=[])
        self.drain()
        self.assertEqual(self.view['project']['events'][-1]['payload']['code'], 'codex_invalid_output')
        self.assertEqual(self.view['project']['candidate'], previous)

    def test_recommendations_use_pinned_project_options_not_the_latest_catalog(self):
        pinned = feature_guidance.catalog()
        booking = next(card for card in pinned['cards'] if card['id'] == 'booking')
        booking['options'] = [row for row in booking['options'] if row['id'] != 'approval']
        with patch('channelshift.feature_guidance.catalog', return_value=pinned):
            self.view = self.pipeline.create('고정 카탈로그 합성 프로젝트', SOURCE, 'service')
        self.project_id = self.view['project']['id']
        self.act('save_features', decisions=[{'id': 'booking', 'option': 'unsure', 'note': ''}])
        self.act('analyze', answers=[])
        self.drain()
        previous = copy.deepcopy(self.view['project']['candidate'])
        self.extract.side_effect = lambda text: dict(candidate(text), feature_recommendations=[
            {'card_id': 'booking', 'option_id': 'approval', 'reason': '담당자 확인', 'tradeoff': '확인 작업 필요'}])
        self.act('analyze', answers=[])
        self.drain()
        self.assertEqual(self.view['project']['events'][-1]['payload']['code'], 'codex_invalid_output')
        self.assertEqual(self.view['project']['candidate'], previous)
        self.extract.side_effect = lambda text: dict(candidate(text), feature_recommendations=[
            {'card_id': 'booking', 'option_id': 'request', 'reason': '예약 접수', 'tradeoff': '별도 답변 필요'}])
        self.act('analyze', answers=[])
        self.drain()
        self.assertEqual(self.view['project']['candidate']['feature_recommendations'][0]['option_id'], 'request')

    def test_legacy_analysis_cannot_create_guided_recommendations_and_empty_is_compatible(self):
        self.view = self.pipeline.create('기존 분석', SOURCE, 'service', guided=False)
        self.project_id = self.view['project']['id']
        self.extract.side_effect = lambda text: dict(candidate(text), feature_recommendations=[])
        self.act('analyze', answers=[])
        self.drain()
        previous = copy.deepcopy(self.view['project']['candidate'])
        self.extract.side_effect = lambda text: dict(candidate(text), feature_recommendations=[
            {'card_id': 'booking', 'option_id': 'approval', 'reason': '담당자 확인', 'tradeoff': '확인 작업 필요'}])
        self.act('analyze', answers=[])
        self.drain()
        self.assertEqual(self.view['project']['events'][-1]['payload']['code'], 'codex_invalid_output')
        self.assertEqual(self.view['project']['candidate'], previous)

    def test_mismatched_frozen_catalog_and_nonconcrete_recommendations_are_rejected(self):
        snapshot = self.view['project']['feature_catalog']
        frozen = {'catalog_version': snapshot['catalog_version'],
                  'decisions': [dict(row, option='unsure') for row in self.selections()]}
        valid = {'card_id': 'booking', 'option_id': 'approval', 'reason': '담당자 확인', 'tradeoff': '확인 작업 필요'}
        for bad in (dict(frozen, catalog_version='other'), dict(frozen, decisions=frozen['decisions'][:-1])):
            with self.assertRaises(ValueError):
                feature_guidance.validate_recommendations(snapshot, bad, [valid])
        for option in ('unsure', 'later', None, 'external-option', []):
            with self.subTest(option=option), self.assertRaises(ValueError):
                feature_guidance.validate_recommendations(snapshot, frozen, [dict(valid, option_id=option)])
        feature_guidance.validate_recommendations(snapshot, frozen, [valid])

    def test_advice_remains_advisory_current_after_receipt_and_stale_after_choice_change(self):
        self.ready()
        self.act('generate', stage='wireframe')
        self.drain()
        self.act('approve', stage='wireframe', note='화면 승인')
        before = copy.deepcopy(self.view)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def advise(stage, context):
            entered.set()
            release.wait(5)
            return advice_result(stage, context)
        self.delivery.advise_callback = Mock(side_effect=advise)
        self.act('advise', stage='features')
        self.assertTrue(entered.wait(3))
        self.assertEqual(self.view['project']['state'], 'ADVISING')
        self.assertTrue(self.view['pipeline']['confirmed'])
        self.assertEqual(self.view['project']['review_context_revision'], before['project']['review_context_revision'])
        self.assertEqual(next(s for s in self.view['pipeline']['stages'] if s['id'] == 'wireframe')['state'], 'approved')
        with self.assertRaisesRegex(ValueError, '^pipeline_busy$'):
            self.act('advise', stage='features')
        release.set()
        self.drain()
        self.assertTrue(self.view['pipeline']['confirmed'])
        row = self.view['pipeline']['advice'][-1]
        self.assertTrue(row['current'])
        self.assertFalse(row['approval_granted'])
        self.assertFalse(row['scope_changed'])
        self.assertEqual(row['result']['stage'], 'features')
        self.assertEqual(self.view['pipeline']['guidance'], before['pipeline']['guidance'])
        self.assertEqual(self.delivery.advise_callback.call_count, 1)
        self.act('save_features', decisions=[{'id': 'booking', 'option': 'none', 'note': '예약 제외'}])
        self.assertFalse(self.view['pipeline']['advice'][-1]['current'])

    def test_bad_advice_is_safe_failure_and_does_not_replace_previous_result(self):
        self.delivery.advise_callback = Mock(side_effect=advice_result)
        self.act('advise', stage='features')
        self.drain()
        saved = copy.deepcopy(self.view['pipeline']['advice'])
        self.delivery.advise_callback.side_effect = lambda stage, context: dict(advice_result(stage, context), approval_granted=True)
        self.act('advise', stage='features')
        self.drain()
        self.assertEqual(self.view['pipeline']['advice'], saved)
        self.assertEqual(self.view['pipeline']['advice_job']['state'], 'failed')
        self.assertEqual(self.view['pipeline']['advice_job']['error'], 'service_unavailable')
        self.assertEqual(len(self.view['pipeline']['guidance']['unresolved']), 8)
        self.delivery.advise_callback.side_effect = RuntimeError('private-provider-secret')
        self.act('advise', stage='features')
        self.drain()
        self.assertNotIn('private-provider-secret', str(self.view))

    def test_advice_late_completion_cannot_overwrite_a_newer_job(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def advise(stage, context):
            entered.set()
            release.wait(5)
            return advice_result(stage, context)
        self.delivery.advise_callback = advise
        self.act('advise', stage='features')
        self.assertTrue(entered.wait(3))
        old_id = self.view['pipeline']['advice_job']['id']
        with self.delivery._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.delivery._event(db, self.project_id, 'workflow_advice_started', {
                'job_id': 'f' * 32, 'stage': 'features', 'previous_state': 'RECEIVED'})
        release.set()
        self.drain()
        self.assertNotEqual(self.view['pipeline']['advice_job']['id'], old_id)
        self.assertEqual(self.view['pipeline']['advice_job']['state'], 'running')
        self.assertEqual(self.view['pipeline']['advice'], [])
        self.assertEqual(self.view['project']['state'], 'ADVISING')

    def test_advice_capacity_and_malformed_inputs_fail_before_callback(self):
        self.delivery.advise_callback = Mock(side_effect=advice_result)
        before = copy.deepcopy(self.view)
        with self.assertRaisesRegex(ValueError, '^invalid_pipeline_input$'):
            self.act('advise', stage='untrusted_stage')
        with self.assertRaisesRegex(ValueError, '^pipeline_artifact_required$'):
            self.act('advise', stage='backend')
        self.assertEqual(self.refresh(), before)
        with self.delivery._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for _ in range(990 - len(self.view['project']['events'])):
                self.delivery._event(db, self.project_id, 'test_history', {})
        self.refresh()
        with self.assertRaisesRegex(ValueError, '^delivery_storage_limit$'):
            self.act('advise', stage='features')
        self.delivery.advise_callback.assert_not_called()
        self.assertEqual(self.refresh()['project']['state'], 'RECEIVED')

    def test_artifact_advice_is_explicit_and_guide_descriptor_reaches_provider_callback(self):
        self.delivery.extract_guided = Mock(side_effect=lambda text, descriptor: candidate(text))
        self.ready()
        self.assertEqual(self.delivery.extract_guided.call_args.args[1], self.view['project']['production_guides'])
        self.act('generate', stage='wireframe')
        self.drain()
        self.delivery.advise_callback = Mock(side_effect=advice_result)
        self.act('advise', stage='wireframe')
        self.drain()
        args = self.delivery.advise_callback.call_args.args
        self.assertEqual(args[0], 'artifacts')
        self.assertEqual(args[1]['artifact_stage'], 'wireframe')
        self.assertTrue(self.view['pipeline']['advice'][-1]['current'])
        self.assertEqual(next(s for s in self.view['pipeline']['stages'] if s['id'] == 'wireframe')['state'], 'generated')


if __name__ == '__main__':
    unittest.main()
