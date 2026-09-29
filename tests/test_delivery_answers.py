"""Question answers and frozen evidence: synthetic inputs, no provider calls."""
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from channelshift.delivery_workspace import DeliveryWorkspace


SOURCE = '회사 소개와 문의 폼이 필요합니다.'
CANDIDATE = {'requirements': [{'id': 'REQ-001', 'text': '문의 폼 제공', 'quote': '문의 폼', 'origin': 'client'}],
             'questions': [{'id': 'Q-001', 'text': '문의 보관 기간은?', 'blocking': True},
                           {'id': 'Q-002', 'text': '담당자는?', 'blocking': False}], 'out_of_scope': []}


class DeliveryAnswerTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='channelshift-answer-test-')
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'delivery.sqlite3'
        self.extract = Mock(side_effect=lambda _: copy.deepcopy(CANDIDATE))
        self.review = Mock(return_value={'advisory_only': True})
        self.workspace = self.open()

    def open(self):
        workspace = DeliveryWorkspace(self.path, extract=self.extract, review=self.review)
        self.addCleanup(workspace.close)
        return workspace

    def run_job(self, project, operation='extract'):
        self.workspace.start(project['id'], operation)
        self.workspace._jobs.submit(lambda: None).result(timeout=5)
        return self.workspace.get(project['id'])

    def candidate(self, source=SOURCE):
        return self.run_job(self.workspace.create('합성 답변 테스트', source))

    @staticmethod
    def body(project, answer='30일 보관합니다.', index=0):
        question = project['question_answers'][index]
        return {'project_id': project['id'], 'candidate_revision': project['candidate_revision'],
                'question_id': question['question_id'], 'question_digest': question['question_digest'],
                'answer': answer, 'expected_revision': question['answer_revision']}

    def test_save_edit_clear_and_restart_append_history_without_models_or_approval(self):
        project = self.candidate()
        source = copy.deepcopy(project['source'])
        first = self.workspace.answer(**self.body(project, '  30일\n보관 😀  '))
        second = self.workspace.answer(**self.body(first, '60일'))
        self.assertEqual(second['question_answers'][0]['answer'], '60일')
        self.assertEqual(second['answer_summary'], {'answered': 1, 'total': 2, 'blocking_unanswered': 0})
        self.assertEqual([row['answer'] for row in second['answer_history']], ['  30일\n보관 😀  ', '60일'])
        self.assertTrue(all(row['approval_granted'] is False for row in second['answer_history']))
        self.assertEqual(second['state'], 'REVIEW_REQUIRED')
        self.assertEqual(self.open().get(project['id']), second)
        cleared = self.workspace.answer(**self.body(second, ''))
        self.assertEqual(cleared['answer_summary']['blocking_unanswered'], 1)
        self.assertEqual(len(cleared['answer_history']), 3)
        self.assertEqual(cleared['source'], source)
        self.assertEqual(self.extract.call_count, 1)
        self.review.assert_not_called()

    def test_revision_and_question_fingerprint_prevent_stale_or_wrong_question_writes(self):
        project = self.candidate()
        saved = self.workspace.answer(**self.body(project))
        for body in (self.body(project, '이전 탭 입력'),
                     {**self.body(saved), 'question_id': 'Q-002'},
                     {**self.body(saved), 'question_digest': '0' * 64},
                     {**self.body(saved), 'candidate_revision': '0' * 64}):
            with self.assertRaisesRegex(ValueError, '^delivery_revision_conflict$'):
                self.workspace.answer(**body)
            self.assertEqual(self.workspace.get(project['id']), saved)

    def test_concurrent_tabs_save_exactly_one_revision_and_other_question_is_independent(self):
        viewed = self.candidate()
        second = self.open()
        barrier = threading.Barrier(2)

        def save(workspace, value):
            barrier.wait(timeout=5)
            try:
                workspace.answer(**self.body(viewed, value))
                return 'saved'
            except ValueError as error:
                return str(error)

        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(save, workspace, value)
                       for workspace, value in ((self.workspace, '30일'), (second, '60일'))]
            self.assertCountEqual([future.result(timeout=5) for future in futures],
                                  ['saved', 'delivery_revision_conflict'])
        result = second.answer(**self.body(viewed, '고객지원 담당자', 1))
        self.assertEqual(result['answer_summary']['answered'], 2)
        self.assertEqual(len(result['answer_history']), 2)

    def test_intervention_revision_binds_latest_answers_even_if_candidate_does_not_change(self):
        viewed = self.candidate()
        answered = self.open().answer(**self.body(viewed, '다른 탭 답변'))
        self.assertEqual(viewed['candidate_revision'], answered['candidate_revision'])
        self.assertNotEqual(viewed['answer_context_digest'], answered['answer_context_digest'])
        self.assertNotEqual(viewed['intervention_revision'], answered['intervention_revision'])
        with self.assertRaisesRegex(ValueError, '^delivery_revision_conflict$'):
            self.workspace.intervene(viewed['id'], 'requirements', 'missing_client_info',
                                     '답변이 없을 때 작성한 메모', '다시 확인', '', viewed['intervention_revision'])
        self.assertEqual(self.workspace.get(viewed['id']), answered)
        current = self.workspace.intervene(viewed['id'], 'requirements', 'missing_client_info',
                                           '새 답변까지 확인한 메모', '추가 검토', '', answered['intervention_revision'])
        self.assertEqual(current['interventions'][-1]['answer_context_digest'], answered['answer_context_digest'])
        # An edit of another question also changes the decision context.
        another = self.workspace.answer(**self.body(current, '담당자 확인', 1))
        self.assertNotEqual(another['intervention_revision'], current['intervention_revision'])
        replacement = self.run_job(another)
        self.assertEqual(replacement['answer_context_digest'], another['answer_context_digest'])

    def test_same_id_and_even_identical_new_candidate_does_not_inherit_old_answer(self):
        previous = self.workspace.answer(**self.body(self.candidate()))
        replacement = self.run_job(previous)
        self.assertNotEqual(previous['candidate_revision'], replacement['candidate_revision'])
        self.assertEqual(replacement['question_answers'][0]['answer'], '')
        self.assertEqual(replacement['answer_summary']['blocking_unanswered'], 1)
        self.assertEqual(replacement['answer_history'], previous['answer_history'])
        with self.assertRaisesRegex(ValueError, '^delivery_revision_conflict$'):
            self.workspace.answer(**self.body(previous, '오래된 질문 답변'))
        different = copy.deepcopy(CANDIDATE)
        different['questions'][0]['text'] = '새 질문: 알림 수신자는?'
        self.extract.side_effect = lambda _: different
        third = self.run_job(replacement)
        self.assertNotEqual(third['question_answers'][0]['question_digest'], previous['question_answers'][0]['question_digest'])
        self.assertEqual(third['question_answers'][0]['answer'], '')

    def test_codex_uses_saved_answers_and_jev_uses_exact_frozen_candidate_input(self):
        first = self.candidate()
        answered = self.workspace.answer(**self.body(first, '문의는 30일 보관합니다.'))
        # A new answer cannot retroactively alter the evidence of the first candidate.
        self.run_job(answered, 'jev')
        self.assertEqual(self.review.call_args.args[0], SOURCE)
        second = self.run_job(answered)
        snapshot = second['candidate_input']
        self.assertEqual(self.extract.call_args.args[0], snapshot['text'])
        self.assertTrue(snapshot['text'].startswith(SOURCE))
        self.assertIn('문의는 30일 보관합니다.', snapshot['text'])
        self.assertEqual(snapshot['digest'], hashlib.sha256(snapshot['text'].encode()).hexdigest())
        self.assertEqual(snapshot['answer_refs'][0]['answer_digest'], answered['answer_history'][0]['answer_digest'])
        current_answered = self.workspace.answer(**self.body(second, '이번 질문의 새 답변'))
        reviewed = self.run_job(current_answered, 'jev')
        self.assertEqual(self.review.call_args.args[0], snapshot['text'])
        self.assertEqual(reviewed['events'][-1]['payload']['input_snapshot'], snapshot)
        # Empty current answer state after a new extraction never loses old evidence.
        third = self.run_job(current_answered)
        self.assertIn('문의는 30일 보관합니다.', third['candidate_input']['text'])
        self.assertIn('이번 질문의 새 답변', third['candidate_input']['text'])
        fourth = self.run_job(third)
        self.assertEqual(fourth['candidate_input'], third['candidate_input'])
        self.assertEqual(fourth['source']['text'], SOURCE)
        self.assertEqual([event for event in fourth['events'] if event['kind'] == 'candidate_recorded'][1]
                         ['payload']['input_snapshot'], snapshot)

    def test_exact_12000_character_combined_input_and_overflow_without_start_or_truncation(self):
        short = self.workspace.answer(**self.body(self.candidate(), '답변'))
        second = self.run_job(short)
        overhead = len(second['candidate_input']['text']) - len(SOURCE)
        at_limit = self.workspace.answer(**self.body(self.candidate('가' * (12000 - overhead)), '답변'))
        result = self.run_job(at_limit)
        self.assertEqual(len(self.extract.call_args.args[0]), 12000)
        self.assertEqual(len(result['candidate_input']['text']), 12000)
        overflow = self.workspace.answer(**self.body(self.candidate('가' * (12001 - overhead)), '답변'))
        before_calls = self.extract.call_count
        with self.assertRaisesRegex(ValueError, '^delivery_input_limit$'):
            self.workspace.start(overflow['id'], 'extract')
        self.assertEqual(self.workspace.get(overflow['id']), overflow)
        self.assertEqual(self.extract.call_count, before_calls)

    def test_answer_limit_counts_unicode_characters_and_invalid_input_has_no_side_effect(self):
        project = self.candidate()
        for value in (None, True, 1, '가' * 2001, '\ud800'):
            with self.assertRaisesRegex(ValueError, '^invalid_delivery_input$'):
                self.workspace.answer(**self.body(project, value))
            self.assertEqual(self.workspace.get(project['id']), project)
        saved = self.workspace.answer(**self.body(project, '😀' * 2000))
        self.assertEqual(saved['question_answers'][0]['answer'], '😀' * 2000)

    def test_failed_rerun_keeps_answers_candidate_and_input_snapshot(self):
        answered = self.workspace.answer(**self.body(self.candidate()))
        candidate = self.run_job(answered)
        self.extract.side_effect = RuntimeError('PRIVATE synthetic exception')
        failed = self.run_job(candidate)
        for key in ('candidate', 'candidate_revision', 'candidate_input', 'answer_history'):
            self.assertEqual(failed[key], candidate[key])
        self.assertNotIn('PRIVATE', json.dumps(failed))
        self.assertIn('30일', failed['events'][-2]['payload']['input_snapshot']['text'])

    def test_running_extraction_freezes_input_and_rejects_concurrent_answer_change(self):
        project = self.workspace.answer(**self.body(self.candidate(), '시작 전 답변'))
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def paused_extract(value):
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError('test timed out')
            return copy.deepcopy(CANDIDATE)

        self.extract.side_effect = paused_extract
        self.workspace.start(project['id'], 'extract')
        self.assertTrue(entered.wait(timeout=5))
        before = self.workspace.get(project['id'])
        try:
            with self.assertRaisesRegex(ValueError, '^delivery_busy$'):
                self.open().answer(**self.body(project, '실행 중 변경'))
            self.assertEqual(self.workspace.get(project['id']), before)
            self.assertIn('시작 전 답변', before['events'][-1]['payload']['input_snapshot']['text'])
        finally:
            release.set()
        self.workspace._jobs.submit(lambda: None).result(timeout=5)
        self.assertEqual(self.workspace.get(project['id'])['candidate_input'],
                         before['events'][-1]['payload']['input_snapshot'])

    def test_oversized_provider_output_is_rejected_without_replacing_old_candidate(self):
        previous = self.candidate()
        oversized = copy.deepcopy(CANDIDATE)
        oversized['requirements'][0]['text'] = '가' * 131073
        self.extract.side_effect = lambda _: oversized
        failed = self.run_job(previous)
        self.assertEqual(failed['state'], 'NEEDS_ATTENTION')
        self.assertEqual(failed['candidate'], previous['candidate'])
        self.assertEqual(failed['events'][-1]['payload']['code'], 'codex_invalid_output')


if __name__ == '__main__':
    unittest.main()
