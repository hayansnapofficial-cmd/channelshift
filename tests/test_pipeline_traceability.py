"""Artifact-derived impact projection, partial evidence and revision boundaries."""
import copy
import json
import unittest
from unittest.mock import patch

from channelshift.impact import analyze_impact
from channelshift.pipeline_traceability import build_traceability
from channelshift.pipeline_workspace import _artifact
from test_pipeline_workspace import erd


def view():
    schema = erd({'name': 'Impact'}, 'sqlite')['schema']
    api = {'operation_link_declarations_checked': True, 'operation_links': [{
        'operation_id': 'readInquiries', 'method': 'GET', 'path': '/api/inquiries',
        'fields': ['inquiries.message'], 'requirement_ids': ['REQ-001'], 'screen_ids': ['SCREEN-001']}]}
    backend = {'route_declarations_checked': True, 'test_definitions_checked': True, 'route_bindings': [{
        'operation_id': 'readInquiries', 'method': 'GET', 'path': '/api/inquiries',
        'handler': 'read_inquiries', 'test_file': 'backend/test_app.py', 'test_symbol': 'Tests.test_read'}]}
    frontend = {'screen_declarations_checked': True, 'screen_bindings': [{
        'screen_id': 'SCREEN-001', 'file': 'frontend/index.html', 'operation_ids': ['readInquiries']}]}
    records = [('erd', _artifact([{'path': 'erd/schema.json', 'content': json.dumps(schema)}])),
               ('api', _artifact([], checks=api)), ('backend', _artifact([], checks=backend)),
               ('frontend', _artifact([], checks=frontend))]
    return {'pipeline': {'revision': 'synthetic-revision', 'stages': [
        {'id': key, 'state': 'approved', 'artifact': artifact} for key, artifact in records]}}, schema


class PipelineTraceabilityTests(unittest.TestCase):
    def test_current_graph_counts_real_declarations_and_does_not_mutate_input(self):
        source, schema = view()
        before = copy.deepcopy(source)
        result = build_traceability(source)
        self.assertEqual(source, before)
        self.assertEqual(result['reason'], 'current')
        self.assertFalse(result['runtime_behavior_verified'])
        impact = analyze_impact(schema, 'inquiries', 'message', result['graph'])
        self.assertEqual(impact['counts'], {'api': 1, 'backend': 1, 'screen': 1, 'test': 1})
        self.assertEqual(len(impact['direct']), 1)
        self.assertEqual(len(impact['indirect']), 3)

    def test_unlinked_field_is_unknown_and_old_schema_digest_cannot_be_reused(self):
        source, schema = view()
        graph = build_traceability(source)['graph']
        result = analyze_impact(schema, 'inquiries', 'id', graph)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertTrue(all(count is None for count in result['counts'].values()))
        schema['entities'][0]['attributes'][1]['nullable'] = True
        self.assertEqual(analyze_impact(schema, 'inquiries', 'message', graph)['reason'], 'schema_revision_mismatch')

    def test_partial_graph_does_not_report_unbuilt_categories_as_zero(self):
        source, schema = view()
        for stage in source['pipeline']['stages']:
            if stage['id'] in {'backend', 'frontend'}:
                stage['state'] = 'stale'
        result = build_traceability(source)
        self.assertEqual(result['reason'], 'contract_incomplete')
        impact = analyze_impact(schema, 'inquiries', 'message', result['graph'])
        self.assertEqual(impact['counts'], {'api': 1, 'backend': None, 'screen': None, 'test': None})
        self.assertEqual(impact['status'], 'PARTIAL')

    def test_missing_validation_evidence_or_stale_erd_never_fabricates_links(self):
        source, _ = view()
        source['pipeline']['stages'][1]['artifact']['checks'] = {}
        self.assertIsNone(build_traceability(source)['graph'])
        source['pipeline']['stages'][0]['state'] = 'stale'
        self.assertEqual(build_traceability(source)['reason'], 'erd_not_current')

    def test_node_and_serialized_size_limits_return_unknown_without_breaking_workspace(self):
        source, _ = view()
        for constant, limit in [('MAX_NODES', 2), ('MAX_EDGES', 1), ('MAX_GRAPH_BYTES', 128)]:
            with self.subTest(constant=constant), patch('channelshift.pipeline_traceability.' + constant, limit):
                result = build_traceability(source)
                self.assertIsNone(result['graph'])
                self.assertEqual(result['reason'], 'traceability_limit')

    def test_shared_handlers_tests_and_screens_are_counted_once(self):
        source, schema = view()
        api, backend, frontend = [stage['artifact']['checks'] for stage in source['pipeline']['stages'][1:]]
        operation = copy.deepcopy(api['operation_links'][0])
        operation.update(operation_id='searchInquiries', path='/api/search')
        api['operation_links'].append(operation)
        binding = copy.deepcopy(backend['route_bindings'][0])
        binding.update(operation_id='searchInquiries', path='/api/search')
        backend['route_bindings'].append(binding)
        frontend['screen_bindings'][0]['operation_ids'].append('searchInquiries')
        graph = build_traceability(source)['graph']
        impact = analyze_impact(schema, 'inquiries', 'message', graph)
        self.assertEqual(impact['counts'], {'api': 2, 'backend': 1, 'screen': 1, 'test': 1})


if __name__ == '__main__':
    unittest.main()
