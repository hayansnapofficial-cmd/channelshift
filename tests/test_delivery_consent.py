"""Local consent-screen preferences do not execute tracking or grant consent."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from channelshift.delivery_workspace import DeliveryWorkspace


class DeliveryConsentTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='channelshift-consent-test-')
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'delivery.sqlite3'
        self.extract, self.review = Mock(), Mock()
        self.workspace = self.open()
        self.project = self.workspace.create('합성 동의 설정', '문의 화면이 필요합니다.')

    def open(self):
        workspace = DeliveryWorkspace(self.path, extract=self.extract, review=self.review)
        self.addCleanup(workspace.close)
        return workspace

    def body(self, project=None, **overrides):
        project = project or self.project
        return {'project_id': project['id'], 'mode': 'required', 'operator_label': '담당자 A',
                'reason': '동의 화면을 검토하려고 선택함', 'expected_revision': project['consent_revision'], **overrides}

    def test_default_and_save_change_restart_preserve_append_only_history_without_authorization(self):
        self.assertEqual(self.project['consent_policy']['mode'], 'undecided')
        self.assertEqual(self.project['consent_history'], [])
        first = self.workspace.consent(**self.body())
        second = self.workspace.consent(**self.body(first, mode='not_required', reason='선택 기능을 사용하지 않을 예정'))
        third = self.workspace.consent(**self.body(second, mode='undecided', reason='서비스 필수 조건 추가 확인'))
        self.assertEqual([item['mode'] for item in third['consent_history']], ['required', 'not_required', 'undecided'])
        self.assertEqual(third['consent_history'][:2], second['consent_history'])
        self.assertEqual(third['source'], self.project['source'])
        self.assertEqual(third['state'], 'RECEIVED')
        for event in third['consent_history']:
            self.assertIsNotNone(datetime.fromisoformat(event['created_at']).tzinfo)
            self.assertFalse(event['tracking_enabled'])
            self.assertFalse(event['approval_granted'])
            self.assertEqual(event['actor_type'], 'local_operator')
        self.assertEqual(self.open().get(self.project['id']), third)
        self.extract.assert_not_called()
        self.review.assert_not_called()

    def test_invalid_missing_actor_reason_and_overlong_fields_do_not_write(self):
        for fields in ({'mode': 'yes'}, {'mode': True}, {'mode': []}, {'operator_label': ''},
                       {'operator_label': '가' * 101}, {'operator_label': '\ud800'},
                       {'reason': '   '}, {'reason': '가' * 2001}, {'reason': False},
                       {'expected_revision': 'z' * 64}):
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, '^invalid_delivery_input$'):
                self.workspace.consent(**self.body(**fields))
            self.assertEqual(self.workspace.get(self.project['id']), self.project)
        with self.assertRaisesRegex(ValueError, '^delivery_project_not_found$'):
            self.workspace.consent(**self.body(project_id='0' * 32))

    def test_stale_and_cross_project_revisions_cannot_change_policy(self):
        second_project = self.workspace.create('다른 합성 프로젝트', self.project['source']['text'])
        with self.assertRaisesRegex(ValueError, '^delivery_revision_conflict$'):
            self.workspace.consent(**self.body(second_project, expected_revision=self.project['consent_revision']))
        saved = self.workspace.consent(**self.body())
        with self.assertRaisesRegex(ValueError, '^delivery_revision_conflict$'):
            self.workspace.consent(**self.body(mode='not_required'))
        self.assertEqual(self.workspace.get(self.project['id']), saved)
        self.assertEqual(self.workspace.get(second_project['id']), second_project)

    def test_concurrent_tabs_exactly_one_save_wins(self):
        other = self.open()
        barrier = threading.Barrier(2)

        def save(workspace, mode):
            barrier.wait(timeout=5)
            try:
                workspace.consent(**self.body(mode=mode))
                return 'saved'
            except ValueError as error:
                return str(error)

        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(save, workspace, mode)
                       for workspace, mode in ((self.workspace, 'required'), (other, 'not_required'))]
            self.assertCountEqual([job.result(timeout=5) for job in futures], ['saved', 'delivery_revision_conflict'])
        self.assertEqual(len(self.workspace.get(self.project['id'])['consent_history']), 1)

    def test_changed_policy_invalidates_intervention_context_and_new_note_binds_revision(self):
        saved = self.workspace.consent(**self.body())
        self.assertNotEqual(saved['intervention_revision'], self.project['intervention_revision'])
        with self.assertRaisesRegex(ValueError, '^delivery_revision_conflict$'):
            self.workspace.intervene(self.project['id'], 'intake', 'other', '미결정 상태에서 작성',
                                     '추가 확인', '', self.project['intervention_revision'])
        self.assertEqual(self.workspace.get(self.project['id']), saved)
        noted = self.workspace.intervene(self.project['id'], 'intake', 'other', '현재 선택 확인',
                                         '법·서비스 조건 별도 검수', '', saved['intervention_revision'])
        self.assertEqual(noted['interventions'][-1]['consent_revision'], saved['consent_revision'])


if __name__ == '__main__':
    unittest.main()
