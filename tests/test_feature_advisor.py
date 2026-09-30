"""Typed workflow advice boundaries. Every provider call is mocked."""
import copy
import io
import json
import sqlite3
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from channelshift import feature_advisor as advisor
from channelshift.jev_review import JevError, ENDPOINT
from channelshift.shared_services import SharedServices, ServiceError


def context():
    return {'source': '예약은 담당자가 확인한 뒤 확정합니다.', 'items': [
        {'id': 'booking', 'text': '담당자 확인 후 예약 확정', 'decision': 'include',
         'note': '주말만 받습니다.', 'evidence': '담당자가 확인한 뒤 확정합니다.', 'evidence_complete': True}]}


def advice(stage='features', value=None, probability=0.9):
    checked = advisor.validate_context(stage, value or context())
    return {'format': advisor.FORMAT, 'stage': stage, 'advisory_only': True, 'approval_granted': False,
            'input_digest': advisor.input_digest(stage, checked), 'model': 'jev-test',
            'judgments': [{'id': item['id'] + '.' + kind, 'item_id': item['id'], 'kind': kind,
                           'probability': probability,
                           'uncertain': not item['evidence_complete'] or 0.2 < probability < 0.8}
                          for _, item, kind in advisor._questions(stage, checked)[1]],
            'usage': {'input_tokens': 10, 'output_tokens': 5}}


class AdvisorTests(unittest.TestCase):
    def request(self, stage='features', value=None, probability=0.9, change=None):
        value = value or context()
        questions = advisor._questions(stage, advisor.validate_context(stage, value))[0]
        reply = {'model': 'jev-test', 'answers': {key: {'type': 'noul', 'noul': probability}
                                               for key in questions},
                 'usage': {'input_tokens': 10, 'output_tokens': 5}}
        if change:
            change(reply)
        opener = MagicMock()
        opener.open.return_value = io.BytesIO(json.dumps(reply).encode())
        with patch('channelshift.feature_advisor.jev_review._credential', return_value='test-only-secret'), \
             patch('channelshift.feature_advisor.urllib.request.build_opener', return_value=opener):
            result = advisor.advise_workflow(stage, value)
        return result, opener

    def test_all_stages_use_narrow_questions_fixed_endpoint_and_no_authority(self):
        for stage in ('features', 'reference', 'artifacts'):
            value = context()
            if stage == 'artifacts':
                value['artifact_stage'] = 'backend'
            result, opener = self.request(stage, value)
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, ENDPOINT)
            self.assertEqual(request.get_header('Authorization'), 'Bearer test-only-secret')
            sent = json.loads(request.data)
            self.assertEqual(len(sent['questions']), 2)
            self.assertEqual({q['type'] for q in sent['questions'].values()}, {'noul'})
            self.assertTrue(result['advisory_only'])
            self.assertFalse(result['approval_granted'])
            self.assertNotIn('test-only-secret', json.dumps(result))
            self.assertNotIn('confidence', json.dumps(result))
            self.assertEqual(opener.open.call_count, 1)

    def test_uncertainty_and_incomplete_evidence_never_enable_a_feature(self):
        for decision in ('exclude', 'unsure', 'later'):
            value = context()
            value['items'][0].update(decision=decision, evidence_complete=False)
            result, _ = self.request(value=value, probability=1)
            self.assertTrue(all(row['uncertain'] for row in result['judgments']))
            self.assertFalse(result['approval_granted'])
            self.assertEqual(value['items'][0]['decision'], decision)
        result, _ = self.request(probability=0.5)
        self.assertTrue(all(row['uncertain'] for row in result['judgments']))

    def test_invalid_context_is_rejected_before_credentials_or_network(self):
        bad = [dict(context(), token='bad'), dict(context(), items=[]), dict(context(), source='x' * 12001)]
        for field, value in [('id', '../../x'), ('decision', 'approved'), ('note', '\ud800'),
                             ('evidence_complete', 1), ('text', 'x' * 2001)]:
            item = copy.deepcopy(context())
            item['items'][0][field] = value
            bad.append(item)
        with patch('channelshift.feature_advisor.jev_review._credential') as credential:
            for value in bad:
                with self.subTest(value=str(value)[:80]), self.assertRaises(JevError):
                    advisor.advise_workflow('features', value)
            credential.assert_not_called()

    def test_artifact_context_can_carry_a_full_bounded_source_file(self):
        value = context()
        value['artifact_stage'] = 'backend'
        value['items'][0]['text'] = 'x' * 114688
        self.assertEqual(len(advisor.validate_context('artifacts', value)['items'][0]['text']), 114688)
        value['items'][0]['text'] = '한' * 100000
        with self.assertRaises(JevError):
            advisor.validate_context('artifacts', value)

    def test_provider_and_callback_results_are_exact_bounded_and_input_bound(self):
        for probability in (True, -1, 2, float('nan')):
            with self.subTest(probability=probability), self.assertRaises(JevError):
                self.request(probability=probability)
        for change in (lambda x: x.update(approval_granted=True),
                       lambda x: x.update(model='secret value'),
                       lambda x: x.update(input_digest='0' * 64),
                       lambda x: x['judgments'][0].update(uncertain=True),
                       lambda x: x['judgments'][0].update(kind='approved')):
            result = advice()
            change(result)
            with self.assertRaises(JevError):
                advisor.validate_workflow_advice(result, 'features', context())

    def test_transport_refuses_proxy_forwarding_redirects_and_safe_errors(self):
        opener = MagicMock()
        opener.open.side_effect = urllib.error.HTTPError(ENDPOINT, 429, 'PRIVATE', {}, None)
        with patch('channelshift.feature_advisor.jev_review._credential', return_value='test-only-secret'), \
             patch('channelshift.feature_advisor.urllib.request.build_opener', return_value=opener) as build:
            with self.assertRaisesRegex(JevError, '^jev_rate_limited$'):
                advisor.advise_workflow('features', context())
        self.assertEqual(build.call_args.args[0].proxies, {})
        with self.assertRaisesRegex(JevError, '^jev_redirect_refused$'):
            build.call_args.args[1].redirect_request(None)


class ServiceAdviceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'usage.sqlite3'
        self.callback = Mock(side_effect=lambda stage, value: advice(stage, value))
        self.services = SharedServices(self.path, advise=self.callback,
            review=Mock(return_value={}), availability={'requirements_review': lambda: True,
                                                       'reference_collect': lambda: False})

    def test_workflow_advice_shares_existing_review_budget_and_validates_first(self):
        member = 'a' * 32
        with self.assertRaisesRegex(ServiceError, '^service_invalid_input$'):
            self.services.advise_workflow(member, 'features', dict(context(), source=''))
        self.callback.assert_not_called()
        for _ in range(10):
            self.services.advise_workflow(member, 'features', context())
        with self.assertRaisesRegex(ServiceError, '^service_rate_limited$'):
            self.services.review_for(member)('Hello', [{'id': 'REQ-001', 'text': 'Hello', 'quote': 'Hello', 'origin': 'client'}])
        with sqlite3.connect(self.path) as db:
            rows = db.execute('SELECT feature,finished FROM feature_usage').fetchall()
        db.close()
        self.assertEqual(rows, [('requirements_review', 1)] * 10)

    def test_bad_callback_is_sanitized_counted_and_cannot_mutate_caller(self):
        value = context()
        def malicious(stage, checked):
            checked['source'] = 'changed'
            return advice(stage, checked)
        self.callback.side_effect = malicious
        with self.assertRaisesRegex(ServiceError, '^service_unavailable$'):
            self.services.advise_workflow('a' * 32, 'features', value)
        self.assertEqual(value, context())
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM feature_usage WHERE finished=1').fetchone()[0], 1)
        db.close()


if __name__ == '__main__':
    unittest.main()
