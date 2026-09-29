"""Persisted requirements review navigation using synthetic local evidence."""
import copy
from concurrent.futures import ThreadPoolExecutor
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from channelshift.delivery_workspace import DeliveryWorkspace
from channelshift.store import ProjectStore
from channelshift.web import handler_factory


CANDIDATE = {
    'requirements': [{'id': 'REQ-001', 'text': '문의 폼 제공', 'quote': '문의 폼', 'origin': 'client'}],
    'questions': [{'id': 'Q-001', 'text': '문의 보관 기간은?', 'blocking': True},
                  {'id': 'Q-002', 'text': '담당자는?', 'blocking': False}],
    'out_of_scope': [],
}


class DeliveryNextStepTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='channelshift-next-step-test-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.path = self.root / 'delivery.sqlite3'
        self.extract = Mock(side_effect=lambda _: copy.deepcopy(CANDIDATE))
        self.review = Mock(return_value={'advisory_only': True})
        self.workspace = self.open()

    def open(self):
        workspace = DeliveryWorkspace(self.path, extract=self.extract, review=self.review)
        self.addCleanup(workspace.close)
        return workspace

    def run_job(self, project):
        self.workspace.start(project['id'], 'extract')
        self.workspace._jobs.submit(lambda: None).result(timeout=5)
        return self.workspace.get(project['id'])

    def candidate(self):
        return self.run_job(self.workspace.create('합성 요구사항 검수', '문의 폼을 제공해 주세요.'))

    def answer(self, project, value='30일', index=0):
        question = project['question_answers'][index]
        return self.workspace.answer(project['id'], project['candidate_revision'], question['question_id'],
                                     question['question_digest'], value, question['answer_revision'])

    def advance(self, project):
        return self.workspace.advance_to_review(project['id'], project['intervention_revision'])

    def test_missing_candidate_and_unanswered_blocker_do_not_write_navigation(self):
        empty = self.workspace.create('합성 접수', '문의 폼을 제공해 주세요.')
        with self.assertRaisesRegex(ValueError, '^delivery_candidate_required$'):
            self.advance(empty)
        project = self.run_job(empty)
        for unanswered in (project, self.answer(project, '  \n ')):
            before = self.workspace.get(project['id'])
            with self.assertRaisesRegex(ValueError, '^delivery_answers_required$'):
                self.advance(before)
            self.assertEqual(self.workspace.get(project['id']), before)
        self.assertEqual(self.workspace.get(project['id'])['workflow_stage'], 'intake')

    def test_review_snapshot_persists_without_approval_or_provider_execution(self):
        answered = self.answer(self.candidate())
        reviewed = self.advance(answered)
        self.assertEqual(reviewed['workflow_stage'], 'requirements_review')
        self.assertEqual(reviewed['state'], 'REVIEW_REQUIRED')
        snapshot = reviewed['requirements_review']
        self.assertEqual(snapshot['candidate'], answered['candidate'])
        self.assertEqual(snapshot['source'], answered['source'])
        self.assertEqual(snapshot['candidate_revision'], answered['candidate_revision'])
        self.assertEqual(snapshot['answer_context_digest'], answered['answer_context_digest'])
        self.assertEqual(snapshot['intervention_revision'], answered['intervention_revision'])
        self.assertEqual(snapshot['answers'][0]['answer'], '30일')
        self.assertEqual(snapshot['answers'][0]['question_text'], '문의 보관 기간은?')
        self.assertEqual(snapshot['answers'][0]['answer_revision'], answered['question_answers'][0]['answer_revision'])
        self.assertEqual(snapshot['answers'][1]['answer'], '')  # Optional question stays optional.
        self.assertFalse(snapshot['approval_granted'])
        self.assertEqual(self.open().get(reviewed['id']), reviewed)
        self.assertEqual(self.extract.call_count, 1)
        self.review.assert_not_called()
        # Retrying with a freshly read revision does not duplicate the snapshot.
        self.assertEqual(self.advance(reviewed), reviewed)

    def test_no_questions_can_advance(self):
        candidate = copy.deepcopy(CANDIDATE)
        candidate['questions'] = []
        self.extract.side_effect = lambda _: candidate
        reviewed = self.advance(self.candidate())
        self.assertEqual(reviewed['workflow_stage'], 'requirements_review')
        self.assertEqual(reviewed['requirements_review']['answers'], [])

    def test_answer_edits_and_candidate_replacement_invalidate_review_but_keep_snapshot(self):
        reviewed = self.advance(self.answer(self.candidate()))
        old_snapshot = copy.deepcopy(reviewed['requirements_review'])
        edited = self.answer(reviewed, '60일')
        self.assertEqual(edited['workflow_stage'], 'intake')
        self.assertIsNone(edited['requirements_review'])
        self.assertEqual(edited['review_history'], [old_snapshot])
        reviewed_again = self.advance(edited)
        replaced = self.run_job(reviewed_again)
        self.assertNotEqual(replaced['candidate_revision'], reviewed_again['candidate_revision'])
        self.assertEqual(replaced['workflow_stage'], 'intake')
        self.assertIsNone(replaced['requirements_review'])
        self.assertEqual(len(replaced['review_history']), 2)
        self.assertEqual(replaced['review_history'][0], old_snapshot)

    def test_consent_revision_and_busy_job_invalidate_review(self):
        reviewed = self.advance(self.answer(self.candidate()))
        changed = self.workspace.consent(reviewed['id'], 'required', '합성 작업자', '검수 테스트',
                                         reviewed['consent_revision'])
        self.assertEqual(changed['workflow_stage'], 'intake')
        reviewed_again = self.advance(changed)
        started, release = threading.Event(), threading.Event()
        def blocked_extract(_):
            started.set()
            release.wait(timeout=5)
            return copy.deepcopy(CANDIDATE)
        self.extract.side_effect = blocked_extract
        try:
            self.workspace.start(reviewed_again['id'], 'extract')
            self.assertTrue(started.wait(timeout=2))
            busy = self.workspace.get(reviewed_again['id'])
            self.assertEqual(busy['workflow_stage'], 'intake')
            with self.assertRaisesRegex(ValueError, '^delivery_busy$'):
                self.advance(busy)
        finally:
            release.set()
            self.workspace._jobs.submit(lambda: None).result(timeout=5)

    def test_next_and_back_compare_and_swap_prevents_stale_navigation(self):
        unanswered = self.candidate()
        answered = self.answer(unanswered)
        with self.assertRaisesRegex(ValueError, '^delivery_revision_conflict$'):
            self.advance(unanswered)
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = [pool.submit(self.advance, answered) for _ in range(2)]
            succeeded, conflicts = [], []
            for attempt in attempts:
                try:
                    succeeded.append(attempt.result())
                except ValueError as error:
                    conflicts.append(str(error))
        self.assertEqual(len(succeeded), 1)
        self.assertEqual(conflicts, ['delivery_revision_conflict'])
        reviewed = succeeded[0]
        self.assertNotEqual(reviewed['intervention_revision'], answered['intervention_revision'])
        with self.assertRaisesRegex(ValueError, '^delivery_revision_conflict$'):
            self.workspace.return_to_intake(answered['id'], answered['intervention_revision'])
        returned = self.workspace.return_to_intake(reviewed['id'], reviewed['intervention_revision'])
        self.assertEqual(returned['workflow_stage'], 'intake')
        self.assertEqual(returned['question_answers'], reviewed['question_answers'])
        self.assertEqual(returned['candidate'], reviewed['candidate'])
        self.assertEqual(returned['events'][-1]['kind'], 'intake_reopened')
        self.assertEqual(self.open().get(returned['id']), returned)
        with self.assertRaisesRegex(ValueError, '^delivery_revision_conflict$'):
            self.advance(reviewed)
        self.assertEqual(self.advance(returned)['workflow_stage'], 'requirements_review')

    def test_invalid_revision_is_rejected_without_events(self):
        project = self.answer(self.candidate())
        for value in (None, '', False, '0' * 63):
            for method in (self.workspace.advance_to_review, self.workspace.return_to_intake):
                with self.assertRaisesRegex(ValueError, '^invalid_delivery_input$'):
                    method(project['id'], value)
        self.assertEqual(self.workspace.get(project['id']), project)

    def test_sequential_answer_save_does_not_absorb_newer_navigation_or_context(self):
        viewed = self.answer(self.candidate())
        reviewed = self.advance(viewed)
        returned = self.workspace.return_to_intake(reviewed['id'], reviewed['intervention_revision'])
        question = viewed['question_answers'][1]
        with self.assertRaisesRegex(ValueError, '^delivery_revision_conflict$'):
            self.workspace.answer(viewed['id'], viewed['candidate_revision'], question['question_id'],
                                  question['question_digest'], '오래된 탭의 담당자', question['answer_revision'],
                                  expected_context_revision=viewed['intervention_revision'])
        self.assertEqual(self.workspace.get(viewed['id']), returned)
        fresh = self.workspace.answer(returned['id'], returned['candidate_revision'], question['question_id'],
                                      question['question_digest'], '합성 담당자', question['answer_revision'],
                                      expected_context_revision=returned['intervention_revision'])
        self.assertEqual(fresh['question_answers'][1]['answer'], '합성 담당자')
        changed = self.workspace.consent(fresh['id'], 'required', '합성 작업자', '정책 갱신', fresh['consent_revision'])
        question = fresh['question_answers'][1]
        with self.assertRaisesRegex(ValueError, '^delivery_revision_conflict$'):
            self.workspace.answer(fresh['id'], fresh['candidate_revision'], question['question_id'],
                                  question['question_digest'], '덮어쓰기 금지', question['answer_revision'],
                                  expected_context_revision=fresh['intervention_revision'])
        self.assertEqual(self.workspace.get(fresh['id']), changed)

    def test_http_routes_require_exact_payload_guard_and_current_revision(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_factory(
            ProjectStore(self.root / 'schemas'), 'next-step-token', delivery=self.workspace))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        def request(path, body, token='next-step-token'):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            try:
                connection.request('POST', path, json.dumps(body), {
                    'Content-Type': 'application/json', 'Origin': origin, 'X-ChannelShift-Token': token})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()
        try:
            project = self.candidate()
            body = {'project_id': project['id'], 'expected_revision': project['intervention_revision']}
            self.assertEqual(request('/api/delivery/next', body)[1]['error'], 'delivery_answers_required')
            project = self.answer(project)
            body['expected_revision'] = project['intervention_revision']
            for path in ('/api/delivery/next', '/api/delivery/back'):
                self.assertEqual(request(path, body, 'wrong-token')[0], 403)
                self.assertEqual(request(path, {**body, 'approval_granted': True})[0], 404)
            status, result = request('/api/delivery/next', body)
            self.assertEqual(status, 200)
            self.assertEqual(result['project']['workflow_stage'], 'requirements_review')
            self.assertEqual(request('/api/delivery/next', body)[0], 409)
            self.assertEqual(request('/api/delivery/back', body)[0], 409)
            body['expected_revision'] = result['project']['intervention_revision']
            status, result = request('/api/delivery/back', body)
            self.assertEqual(status, 200)
            self.assertEqual(result['project']['workflow_stage'], 'intake')
            self.assertEqual(self.extract.call_count, 1)
            self.review.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
