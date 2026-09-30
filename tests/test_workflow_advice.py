"""Advisory evidence isolation and staleness, without external service calls."""
import copy
from pathlib import Path
import tempfile
import unittest

from channelshift.delivery_workspace import DeliveryWorkspace
from channelshift.workflow_advice import build_context


class AdviceContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='channelshift-advice-context-')
        self.addCleanup(self.temp.cleanup)
        self.delivery = DeliveryWorkspace(Path(self.temp.name) / 'delivery.sqlite3')
        self.addCleanup(self.delivery.close)
        project = self.delivery.create('합성 검수', '고객이 문의를 남기는 홈페이지입니다.', guided=True)
        self.view = {'project': project, 'pipeline': {'stages': [], 'revision': 'old'}}

    def test_features_no_selection_is_uncertain_and_advice_does_not_change_basis(self):
        provider, context, digest = build_context(self.view, 'features')
        self.assertEqual(provider, 'features')
        self.assertEqual(len(context['items']), 8)
        self.assertTrue(all(row['decision'] == 'unsure' for row in context['items']))
        changed = copy.deepcopy(self.view)
        changed['pipeline']['revision'] = 'new'
        changed['pipeline']['advice'] = [{'result': 'advice only'}]
        changed['project']['workflow_advice'] = [{'result': 'advice only'}]
        self.assertEqual(build_context(changed, 'features')[2], digest)
        changed['project']['guidance']['cards'][0]['note'] = '상담 예약은 제외합니다.'
        self.assertNotEqual(build_context(changed, 'features')[2], digest)

    def test_reference_is_separate_and_partial_source_is_explicit(self):
        self.view['project']['references'] = [{
            'url': 'https://example.com/', 'title': '참고 디자인',
            'text': '외부에서만 존재하는 문장' + 'x' * 2100, 'truncated': False}]
        provider, context, digest = build_context(self.view, 'reference')
        self.assertEqual(provider, 'reference')
        self.assertNotIn('외부에서만 존재하는 문장', context['source'])
        self.assertIn('외부에서만 존재하는 문장', context['items'][0]['evidence'])
        self.assertFalse(context['items'][0]['evidence_complete'])
        # Even a change outside the transmitted excerpt makes previous advice old.
        self.view['project']['references'][0]['text'] += '변경'
        self.assertNotEqual(build_context(self.view, 'reference')[2], digest)

    def test_artifact_full_files_are_bound_and_stale_outputs_cannot_be_reviewed(self):
        stage = {'id': 'backend', 'state': 'generated', 'input_key': 'v1',
                 'artifact': {'files': [{'path': 'backend/app.py', 'content': 'print("example")'}],
                              'notes': [], 'digest': 'old'}}
        self.view['pipeline']['stages'].append(stage)
        provider, context, digest = build_context(self.view, 'backend')
        self.assertEqual(provider, 'artifacts')
        self.assertEqual(context['artifact_stage'], 'backend')
        self.assertIn('backend/app.py', context['items'][0]['text'])
        self.assertTrue(context['items'][0]['evidence_complete'])
        stage['artifact']['files'][0]['content'] = 'print("changed")'
        self.assertNotEqual(build_context(self.view, 'backend')[2], digest)
        stage['state'] = 'stale'
        with self.assertRaisesRegex(ValueError, 'pipeline_artifact_stale'):
            build_context(self.view, 'backend')

    def test_empty_reference_and_unknown_stage_fail_before_provider(self):
        with self.assertRaisesRegex(ValueError, 'delivery_candidate_required'):
            build_context(self.view, 'reference')
        with self.assertRaisesRegex(ValueError, 'invalid_pipeline_input'):
            build_context(self.view, 'untrusted-provider-name')


if __name__ == '__main__':
    unittest.main()
