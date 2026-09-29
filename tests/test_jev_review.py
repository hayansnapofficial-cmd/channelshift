import copy
import io
import http.client
import json
import unittest
import urllib.error
from unittest.mock import patch, MagicMock

from channelshift.jev_review import ENDPOINT, JevError, review_requirements


class JevReviewTests(unittest.TestCase):
    def setUp(self):
        self.source = '문의는 관리자만 볼 수 있습니다.'
        self.rows = [{'id': 'REQ-001', 'text': '관리자만 문의 조회', 'quote': self.source, 'origin': 'client'}]
        self.reply = {'model': 'jev-test', 'answers': {'candidate_0': {'type': 'choice', 'choice': 'supported',
                      'confidence': 0.91, 'probabilities': {'supported': 0.94, 'unsupported': 0.02, 'contradicted': 0.01, 'unclear': 0.03}}},
                      'usage': {'input_tokens': 100, 'output_tokens': 20}}

    def run_review(self, reply=None):
        opener = MagicMock()
        opener.open.return_value = io.BytesIO(json.dumps(reply or self.reply).encode())
        with patch('channelshift.jev_review._credential', return_value='test-only-secret'), \
             patch('channelshift.jev_review.urllib.request.build_opener', return_value=opener):
            result = review_requirements(self.source, self.rows)
        return result, opener

    def test_success_is_advice_not_approval_and_uses_fixed_endpoint(self):
        result, opener = self.run_review()
        self.assertEqual(result['status'], 'REVIEW_REQUIRED')
        self.assertFalse(result['approved'])
        self.assertNotIn('test-only-secret', json.dumps(result))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, ENDPOINT)
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-only-secret')
        self.assertEqual(json.loads(request.data)['state']['client_source'], self.source)
        self.assertEqual(opener.open.call_count, 1)

    def test_source_and_candidate_are_bound_to_advice(self):
        first, _ = self.run_review()
        self.rows[0]['text'] += ' 기능'
        second, _ = self.run_review()
        self.assertNotEqual(first['input_digest'], second['input_digest'])

    def test_fabricated_quote_and_internal_quote_never_call_service(self):
        with patch('channelshift.jev_review._credential') as credential:
            self.rows[0]['quote'] = '없는 인용문'
            with self.assertRaisesRegex(JevError, 'jev_quote_not_in_source'):
                review_requirements(self.source, self.rows)
            self.rows[0]['origin'] = 'internal'
            with self.assertRaisesRegex(JevError, 'invalid_jev_input'):
                review_requirements(self.source, self.rows)
            credential.assert_not_called()

    def test_uncertainty_and_unsupported_results_never_auto_approve(self):
        for judgment in ('unclear', 'unsupported', 'contradicted'):
            self.reply['answers']['candidate_0']['choice'] = judgment
            self.reply['answers']['candidate_0']['probabilities'] = {key: int(key == judgment) for key in ('supported', 'unsupported', 'contradicted', 'unclear')}
            result, _ = self.run_review()
            self.assertFalse(result['approved'])
            self.assertEqual(result['items'][0]['judgment'], judgment)

    def test_malformed_provider_output_is_rejected(self):
        bad = []
        for field, value in [('confidence', True), ('confidence', float('nan')), ('confidence', 10**1000), ('choice', 'approved'), ('choice', 'unsupported'),
                             ('probabilities', {'supported': 1}), ('type', 'score')]:
            reply = copy.deepcopy(self.reply)
            reply['answers']['candidate_0'][field] = value
            bad.append(reply)
        reply = copy.deepcopy(self.reply)
        reply['answers']['candidate_0']['probabilities']['supported'] = 0
        bad.append(reply)
        bad.append(dict(self.reply, answers={}))
        bad.append(dict(self.reply, usage={'input_tokens': True, 'output_tokens': 20}))
        for reply in bad:
            with self.subTest(reply=reply), self.assertRaisesRegex(JevError, 'jev_invalid_response'):
                self.run_review(reply)

    def test_provider_errors_are_safe_and_not_retried(self):
        for error, code in [(urllib.error.HTTPError(ENDPOINT, 401, 'PRIVATE', {}, None), 'jev_auth_failed'),
                            (urllib.error.HTTPError(ENDPOINT, 429, 'PRIVATE', {}, None), 'jev_rate_limited'),
                            (urllib.error.URLError('PRIVATE'), 'jev_unavailable'),
                            (http.client.BadStatusLine('PRIVATE'), 'jev_unavailable'),
                            (JevError('jev_redirect_refused'), 'jev_redirect_refused')]:
            opener = MagicMock()
            opener.open.side_effect = error
            with patch('channelshift.jev_review._credential', return_value='test-only-secret'), \
                 patch('channelshift.jev_review.urllib.request.build_opener', return_value=opener):
                with self.assertRaises(JevError) as caught:
                    review_requirements(self.source, self.rows)
            self.assertEqual(str(caught.exception), code)
            self.assertEqual(opener.open.call_count, 1)

    def test_input_size_and_duplicate_limits(self):
        for source, rows in [('', self.rows), ('x' * 12001, self.rows), (self.source, []),
                             (self.source, self.rows * 2), (self.source, [dict(self.rows[0], approved=True)])]:
            with self.subTest(source_length=len(source)), self.assertRaises(JevError):
                review_requirements(source, rows)


if __name__ == '__main__':
    unittest.main()
