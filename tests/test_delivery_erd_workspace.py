"""Requirement-bound ERD draft history, using only injected local providers."""
import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from channelshift.codex_intake import CodexIntakeError
from channelshift.delivery_workspace import DeliveryWorkspace


SOURCE = '문의 폼을 제공해 주세요.'
CANDIDATE = {
    'requirements': [{'id': 'REQ-001', 'text': '문의 폼 제공', 'quote': '문의 폼', 'origin': 'client'},
                     {'id': 'REQ-002', 'text': '내부 알림 제안', 'quote': '', 'origin': 'internal'}],
    'questions': [{'id': 'Q-001', 'text': '문의 보관 기간은?', 'blocking': True},
                  {'id': 'Q-002', 'text': '문의 담당자는?', 'blocking': False}],
    'out_of_scope': [],
}


def design(name='합성 ERD 프로젝트', database='postgresql'):
    return {'schema': {'format': 'channelshift.schema/v1', 'name': name, 'database': database,
                       'entities': [{'name': 'inquiries', 'description': '문의 기록', 'attributes': [
                           {'name': 'id', 'type': 'bigint', 'nullable': False,
                            'primary_key': True, 'unique': False}]}], 'relations': []},
            'traceability': [{'entity': 'inquiries', 'requirement_ids': ['REQ-001']}],
            'unmapped_requirements': [], 'notes': ['검토용 초안입니다.']}


class DeliveryErdWorkspaceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='channelshift-erd-test-')
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'delivery.sqlite3'
        self.extract = Mock(side_effect=lambda _: copy.deepcopy(CANDIDATE))
        self.review = Mock(return_value={'approved': False})
        self.collect = Mock(return_value={'text': 'reference', 'approved': False})
        self.generator = Mock(side_effect=lambda snapshot, database: design(snapshot['name'], database))
        self.workspace = self.open()

    def open(self, generator=None, use_default=False):
        workspace = DeliveryWorkspace(self.path, extract=self.extract, review=self.review,
            collect=self.collect, generate_erd=None if use_default else (generator or self.generator))
        self.addCleanup(workspace.close)
        return workspace

    def drain(self, workspace=None):
        (workspace or self.workspace)._jobs.submit(lambda: None).result(timeout=5)

    def candidate(self, name='합성 ERD 프로젝트'):
        project = self.workspace.create(name, SOURCE)
        self.workspace.start(project['id'], 'extract')
        self.drain()
        return self.workspace.get(project['id'])

    def answer(self, project, value='30일', index=0):
        question = project['question_answers'][index]
        return self.workspace.answer(project['id'], project['candidate_revision'], question['question_id'],
            question['question_digest'], value, question['answer_revision'])

    def advance(self, project):
        return self.workspace.advance_to_review(project['id'], project['intervention_revision'])

    def ready(self, name='합성 ERD 프로젝트'):
        return self.advance(self.answer(self.candidate(name)))

    def generate(self, project, database='postgresql', workspace=None):
        workspace = workspace or self.workspace
        workspace.generate_erd(project['id'], database, project['intervention_revision'])
        self.drain(workspace)
        return workspace.get(project['id'])

    def test_requires_current_review_answers_and_real_client_requirement(self):
        empty = self.workspace.create('접수', SOURCE)
        with self.assertRaisesRegex(ValueError, '^delivery_review_required$'):
            self.generate(empty)
        unanswered = self.candidate()
        with self.assertRaisesRegex(ValueError, '^delivery_answers_required$'):
            self.generate(unanswered)
        answered = self.answer(unanswered)
        with self.assertRaisesRegex(ValueError, '^delivery_review_required$'):
            self.generate(answered)
        only_internal = copy.deepcopy(CANDIDATE)
        only_internal['requirements'] = [only_internal['requirements'][1]]
        only_internal['questions'] = []
        self.extract.side_effect = lambda _: copy.deepcopy(only_internal)
        reviewed = self.advance(self.candidate('내부 제안'))
        with self.assertRaisesRegex(ValueError, '^delivery_client_requirements_required$'):
            self.generate(reviewed)
        self.generator.assert_not_called()
        self.assertFalse(any(event['kind'].startswith('erd_') for event in self.workspace.get(reviewed['id'])['events']))

    def test_generation_records_frozen_lineage_without_changing_evidence(self):
        reviewed = self.ready()
        generated = self.generate(reviewed, 'mysql')
        snapshot, database = self.generator.call_args.args
        self.assertEqual(snapshot, dict(reviewed['requirements_review'], name=reviewed['name']))
        self.assertEqual(database, 'mysql')
        for key in ('source', 'candidate', 'jev', 'state', 'requirements_review', 'review_context_revision'):
            self.assertEqual(generated[key], reviewed[key], key)
        self.assertNotEqual(generated['intervention_revision'], reviewed['intervention_revision'])
        self.assertTrue(generated['erd_current'])
        draft = generated['erd_draft']
        self.assertEqual(draft['result'], design(reviewed['name'], 'mysql'))
        self.assertEqual(draft['database'], 'mysql')
        self.assertEqual(draft['review_context_revision'], reviewed['review_context_revision'])
        self.assertEqual(draft['source_digest'], reviewed['source']['digest'])
        self.assertEqual(draft['candidate_revision'], reviewed['candidate_revision'])
        self.assertTrue(draft['traceability_current'])
        self.assertFalse(draft['stale'])
        self.assertFalse(draft['approval_granted'])
        self.assertFalse(draft['database_executed'])
        self.assertEqual(self.open().get(generated['id']), generated)
        self.review.assert_not_called()
        self.collect.assert_not_called()

    def test_invalid_database_and_stale_retries_never_launch_another_generation(self):
        reviewed = self.ready()
        for database in ('oracle', '', None, []):
            with self.subTest(database=database), self.assertRaisesRegex(ValueError, '^invalid_delivery_input$'):
                self.workspace.generate_erd(reviewed['id'], database, reviewed['intervention_revision'])
        generated = self.generate(reviewed)
        with self.assertRaisesRegex(ValueError, '^delivery_revision_conflict$'):
            self.generate(reviewed)
        self.assertEqual(self.generator.call_count, 1)
        self.assertEqual(self.workspace.get(reviewed['id']), generated)

    def test_designing_is_busy_but_does_not_temporarily_invalidate_review(self):
        reviewed = self.ready()
        started, release = threading.Event(), threading.Event()
        def blocked(snapshot, database):
            started.set()
            release.wait(timeout=5)
            return design(snapshot['name'], database)
        self.generator.side_effect = blocked
        other = self.open()
        try:
            self.workspace.generate_erd(reviewed['id'], 'postgresql', reviewed['intervention_revision'])
            self.assertTrue(started.wait(timeout=2))
            busy = self.workspace.get(reviewed['id'])
            self.assertEqual(busy['state'], 'DESIGNING_ERD')
            self.assertEqual(busy['review_context_revision'], reviewed['review_context_revision'])
            self.assertEqual(busy['requirements_review'], reviewed['requirements_review'])
            for action in (lambda: self.workspace.start(busy['id'], 'extract'),
                           lambda: self.workspace.collect_reference(busy['id'], 'https://example.com/', busy['intervention_revision']),
                           lambda: self.workspace.advance_to_review(busy['id'], busy['intervention_revision']),
                           lambda: self.workspace.return_to_intake(busy['id'], busy['intervention_revision']),
                           lambda: self.answer(busy),
                           lambda: self.workspace.save_erd(busy['id'], design()['schema'], 'a' * 64)):
                with self.assertRaisesRegex(ValueError, '^delivery_busy$'):
                    action()
            for action in (lambda: other.generate_erd(busy['id'], 'sqlite', busy['intervention_revision']),
                           lambda: other.start(busy['id'], 'jev'),
                           lambda: other.collect_reference(busy['id'], 'https://example.com/', busy['intervention_revision'])):
                with self.assertRaisesRegex(ValueError, '^delivery_recovery_required$'):
                    action()
        finally:
            release.set()
            self.drain()
        self.assertEqual(self.workspace.get(reviewed['id'])['state'], reviewed['state'])

    def test_unfinished_erd_job_blocks_other_project_after_restart(self):
        reviewed = self.ready()
        another = self.ready('두 번째 프로젝트')
        with self.workspace._connect() as db:
            db.execute("UPDATE projects SET state='DESIGNING_ERD' WHERE id=?", (reviewed['id'],))
        reopened = self.open()
        with self.assertRaisesRegex(ValueError, '^delivery_recovery_required$'):
            self.generate(another, workspace=reopened)
        self.generator.assert_not_called()

    def test_consent_change_during_generation_keeps_snapshot_and_marks_result_stale(self):
        reviewed = self.ready()
        started, release = threading.Event(), threading.Event()
        def blocked(snapshot, database):
            started.set()
            release.wait(timeout=5)
            return design(snapshot['name'], database)
        self.generator.side_effect = blocked
        try:
            self.workspace.generate_erd(reviewed['id'], 'sqlite', reviewed['intervention_revision'])
            self.assertTrue(started.wait(timeout=2))
            changed = self.workspace.consent(reviewed['id'], 'required', '합성 작업자', '정보 수집 확인', reviewed['consent_revision'])
        finally:
            release.set()
            self.drain()
        result = self.workspace.get(reviewed['id'])
        self.assertEqual(result['consent_revision'], changed['consent_revision'])
        self.assertEqual(result['state'], reviewed['state'])
        self.assertIsNone(result['requirements_review'])
        self.assertFalse(result['erd_current'])
        self.assertTrue(result['erd_draft']['stale'])
        self.assertEqual(result['erd_draft']['review_context_revision'], reviewed['review_context_revision'])

    def test_answer_changes_invalidate_draft_without_erasing_history(self):
        generated = self.generate(self.ready())
        old = copy.deepcopy(generated['erd_draft'])
        changed = self.answer(generated, '담당자', index=1)
        self.assertFalse(changed['erd_current'])
        self.assertTrue(changed['erd_draft']['stale'])
        self.assertEqual(changed['erd_draft']['result'], old['result'])
        with self.assertRaisesRegex(ValueError, '^delivery_erd_stale$'):
            self.workspace.save_erd(changed['id'], old['result']['schema'], old['revision'])
        reviewed = self.advance(changed)
        self.assertFalse(reviewed['erd_current'])
        regenerated = self.generate(reviewed)
        self.assertTrue(regenerated['erd_current'])
        self.assertEqual(len(regenerated['erd_history']), 2)
        self.assertTrue(regenerated['erd_history'][0]['stale'])
        self.assertFalse(regenerated['erd_history'][1]['stale'])

    def test_failed_or_invalid_output_preserves_original_state_and_prior_draft(self):
        generated = self.generate(self.ready())
        old = copy.deepcopy(generated['erd_draft'])
        for error, code in [(CodexIntakeError('codex_authentication_required'), 'codex_authentication_required'),
                            (RuntimeError('PRIVATE-MARKER'), 'delivery_job_failed')]:
            self.generator.side_effect = error
            failed = self.generate(self.workspace.get(generated['id']))
            self.assertEqual(failed['state'], generated['state'])
            self.assertEqual(failed['erd_draft'], old)
            self.assertTrue(failed['erd_current'])
            event = failed['events'][-1]
            self.assertEqual(event['kind'], 'erd_failed')
            self.assertEqual(event['payload']['code'], code)
            self.assertNotIn('PRIVATE-MARKER', str(failed))
        self.generator.side_effect = lambda snapshot, database: dict(design(snapshot['name'], database), approved=True)
        failed = self.generate(self.workspace.get(generated['id']))
        self.assertEqual(failed['events'][-1]['payload']['code'], 'codex_invalid_erd_output')
        self.assertEqual(failed['erd_draft'], old)

    def test_manual_edit_keeps_lineage_but_invalidates_generated_mapping_claim(self):
        generated = self.generate(self.ready())
        draft = generated['erd_draft']
        edited = copy.deepcopy(draft['result']['schema'])
        edited['entities'][0]['name'] = 'requests'
        saved = self.workspace.save_erd(generated['id'], edited, draft['revision'])
        updated = saved['erd_draft']
        self.assertTrue(saved['erd_current'])
        self.assertEqual(updated['result']['schema'], edited)
        self.assertFalse(updated['traceability_current'])
        self.assertEqual(updated['result']['traceability'], draft['result']['traceability'])
        self.assertEqual(updated['generation_id'], draft['generation_id'])
        self.assertEqual(updated['review_context_revision'], draft['review_context_revision'])
        self.assertEqual(updated['previous_revision'], draft['revision'])
        self.assertNotEqual(updated['revision'], draft['revision'])
        self.assertEqual(saved['review_context_revision'], generated['review_context_revision'])
        self.assertNotEqual(saved['intervention_revision'], generated['intervention_revision'])
        self.assertEqual(len(saved['erd_history']), 2)
        self.assertTrue(saved['erd_history'][0]['stale'])
        self.assertFalse(updated['approval_granted'])
        self.assertFalse(updated['database_executed'])
        self.assertEqual(saved['events'][-1]['kind'], 'erd_edited')
        self.assertEqual(self.generator.call_count, 1)
        with self.assertRaisesRegex(ValueError, '^delivery_revision_conflict$'):
            self.workspace.save_erd(generated['id'], edited, draft['revision'])

    def test_manual_edit_validates_schema_and_only_one_concurrent_save_wins(self):
        reviewed = self.ready()
        with self.assertRaisesRegex(ValueError, '^delivery_erd_required$'):
            self.workspace.save_erd(reviewed['id'], design()['schema'], 'a' * 64)
        generated = self.generate(reviewed)
        draft = generated['erd_draft']
        with self.assertRaisesRegex(ValueError, '^invalid_schema$'):
            self.workspace.save_erd(generated['id'], {'sql': 'DROP DATABASE anything'}, draft['revision'])
        edited = copy.deepcopy(draft['result']['schema'])
        edited['entities'][0]['description'] = '작업자가 수정한 문의 기록'
        def save():
            try:
                return self.workspace.save_erd(generated['id'], edited, draft['revision'])
            except ValueError as error:
                return str(error)
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = list(pool.map(lambda _: save(), range(2)))
        self.assertEqual(sum(type(value) is dict for value in attempts), 1)
        self.assertEqual(attempts.count('delivery_revision_conflict'), 1)
        self.assertEqual(len(self.workspace.get(generated['id'])['erd_history']), 2)

    def test_saving_unchanged_schema_is_noop_but_still_checks_revision_and_staleness(self):
        generated = self.generate(self.ready())
        draft = generated['erd_draft']
        unchanged = self.workspace.save_erd(generated['id'], copy.deepcopy(draft['result']['schema']), draft['revision'])
        self.assertEqual(unchanged, generated)
        self.assertTrue(unchanged['erd_draft']['traceability_current'])
        with self.assertRaisesRegex(ValueError, '^delivery_revision_conflict$'):
            self.workspace.save_erd(generated['id'], draft['result']['schema'], 'a' * 64)
        self.answer(generated, '60일')
        with self.assertRaisesRegex(ValueError, '^delivery_erd_stale$'):
            self.workspace.save_erd(generated['id'], draft['result']['schema'], draft['revision'])

    def test_default_generator_is_lazy_and_callback_cannot_mutate_frozen_evidence(self):
        reviewed = self.ready()
        default = self.open(use_default=True)
        with patch('channelshift.codex_erd.generate_erd', side_effect=lambda snapshot, database: design(snapshot['name'], database)) as generate:
            generate.assert_not_called()
            saved = self.generate(reviewed, workspace=default)
            generate.assert_called_once()
        def mutate(snapshot, database):
            snapshot['candidate']['requirements'][0]['id'] = 'REQ-999'
            value = design(snapshot['name'], database)
            value['traceability'][0]['requirement_ids'] = ['REQ-999']
            return value
        self.generator.side_effect = mutate
        failed = self.generate(saved)
        self.assertEqual(failed['events'][-1]['payload']['code'], 'codex_invalid_erd_output')
        self.assertEqual(failed['candidate'], reviewed['candidate'])
        started = [event for event in failed['events'] if event['kind'] == 'erd_started'][-1]
        self.assertEqual(started['payload']['input_snapshot']['candidate'], reviewed['candidate'])


if __name__ == '__main__':
    unittest.main()
