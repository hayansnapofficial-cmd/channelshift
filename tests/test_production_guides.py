"""Version pinning and Codex scope/recommendation prompt contracts; no live calls."""
import json
import unittest
from unittest.mock import patch

from channelshift import production_guides as guides
from channelshift import codex_intake as intake
from channelshift import codex_erd as erd
from channelshift import codex_pipeline as pipeline
from channelshift import site_obligations

SOURCE = '회사 소개 문구를 보여 주세요.'


def candidate():
    return {'requirements': [{'id': 'REQ-001', 'text': '회사 소개 문구를 표시한다.',
                              'quote': SOURCE, 'origin': 'client'}], 'questions': [], 'out_of_scope': []}


def snapshot():
    return {'name': '회사 소개', 'source': {'text': SOURCE}, 'candidate': candidate(),
            'answers': [], 'production_guides': guides.descriptor()}


def recommendation():
    return {'card_id': 'booking', 'option_id': 'none', 'reason': '소개용 사이트에는 예약을 받지 않아도 됩니다.',
            'tradeoff': '예약이 필요해지면 별도 문의 경로를 추가해야 합니다.'}


class GuideTests(unittest.TestCase):
    def test_descriptor_is_detached_and_every_stage_has_versioned_guidance(self):
        first = guides.descriptor()
        changed = guides.descriptor()
        changed['sections'].clear()
        self.assertEqual(first, guides.descriptor('v1'))
        # Existing projects pin this exact v1 text; edits require a new version.
        self.assertEqual(first['digest'], 'edbb90b6fde5c74099901490bb6454afd0831cb6123bda7cc07e3c8377e6802a')
        for stage in ('requirements', 'wireframe', 'erd', 'api', 'database', 'backend', 'frontend', 'delivery'):
            self.assertIn('v1', guides.instructions(stage))
            self.assertIn('documents_qa:', guides.instructions(stage))
        self.assertIn('design:', guides.instructions('wireframe'))
        self.assertIn('implementation:', guides.instructions('backend'))
        for value in (dict(first, digest='0' * 64), dict(first, version='v999'), dict(first, instructions='injected')):
            with self.assertRaisesRegex(ValueError, '^invalid_production_guides$'):
                guides.validate_descriptor(value)

    def test_intake_receives_scope_controls_complete_catalog_and_optional_advice(self):
        result = candidate()
        result['feature_recommendations'] = [recommendation()]
        with patch.object(intake, '_execute_json', return_value=result) as execute:
            self.assertEqual(intake.extract_requirements(SOURCE), result)
        prompt, schema = execute.call_args.args
        instruction, raw = prompt.split('\n', 1)
        sent = json.loads(raw)
        self.assertEqual(sent['production_guides'], guides.descriptor())
        self.assertEqual(len(sent['feature_catalog']['cards']), 8)
        self.assertIn('blocking clarification', instruction)
        self.assertIn('unsure, later and unselected', instruction)
        self.assertIn('until the operator explicitly accepts', instruction)
        self.assertIn('feature_recommendations', schema['required'])

    def test_old_candidates_preserved_and_recommendation_shape_strict(self):
        self.assertEqual(intake.validate_candidate(candidate(), SOURCE), candidate())
        for field, value in [('card_id', 'unknown'), ('option_id', 'unknown'), ('option_id', 'unsure'),
                             ('option_id', 'later'), ('reason', ''), ('reason', 'x' * 501),
                             ('tradeoff', '\ud800'), ('approval_granted', True)]:
            bad = candidate()
            bad['feature_recommendations'] = [dict(recommendation(), **{field: value})]
            with self.subTest(field=field, value=str(value)[:20]), self.assertRaises(intake.CodexIntakeError):
                intake.validate_candidate(bad, SOURCE)
        bad = candidate()
        bad['feature_recommendations'] = [recommendation(), recommendation()]
        with self.assertRaises(intake.CodexIntakeError):
            intake.validate_candidate(bad, SOURCE)

    def test_guides_reach_erd_but_feature_recommendations_do_not_authorize_data(self):
        saved = snapshot()
        saved['candidate']['feature_recommendations'] = [recommendation()]
        value = {'schema': {'format': 'channelshift.schema/v1', 'name': saved['name'],
                           'database': 'sqlite', 'entities': [], 'relations': []}, 'traceability': [],
                 'unmapped_requirements': [{'requirement_id': 'REQ-001', 'reason': '정적 소개 문구입니다.'}], 'notes': []}
        with patch.object(intake, '_execute_json', return_value=value) as execute:
            erd.generate_erd(saved, 'sqlite')
        prompt = execute.call_args.args[0]
        instruction, raw = prompt.split('\n', 1)
        sent = json.loads(raw)
        self.assertEqual(sent['production_guides'], guides.descriptor())
        self.assertNotIn('feature_recommendations', raw)
        self.assertIn('implementation:', instruction)

    def test_pipeline_preserves_pinned_guide_and_rejects_tampering_before_call(self):
        spec = {'name': '회사 소개', 'database': 'sqlite', 'requirements': snapshot(),
                'obligations': site_obligations.catalog()['empty_values'], 'production_guides': guides.descriptor()}
        value = {'files': [{'path': 'wireframe/index.html', 'content': '<html><body>소개</body></html>'},
                           {'path': 'wireframe/screens.json', 'content': '{}'}], 'notes': []}
        with patch.object(intake, '_execute_json', return_value=value) as execute:
            pipeline.generate_stage('wireframe', spec, {})
        prompt = execute.call_args.args[0]
        instruction, raw = prompt.split('\n', 1)
        self.assertIn('design:', instruction)
        self.assertEqual(json.loads(raw)['confirmed_spec']['production_guides'], guides.descriptor())
        spec['requirements']['production_guides']['digest'] = '0' * 64
        with patch.object(intake, '_execute_json') as execute, self.assertRaises(intake.CodexIntakeError):
            pipeline.generate_stage('wireframe', spec, {})
        execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
