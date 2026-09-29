"""Registered dependency paths, unknown coverage and exact revision binding."""
import copy
import hashlib
import unittest

from channelshift.core import create_schema
from channelshift.impact import analyze_impact, field_node_id, schema_digest, validate_graph
from channelshift.store import canonical


def fixture(covered=True):
    schema = create_schema('booking', '영향 분석 예시', 'postgresql')
    field = field_node_id('customers', 'email')
    graph = {'format': 'channelshift.traceability/v1', 'schema_digest': schema_digest(schema),
             'nodes': [{'id': field, 'kind': 'field', 'label': '고객 이메일'}], 'edges': []}
    if covered:
        graph['covered_kinds'] = ['api', 'backend', 'screen', 'test']
    return schema, graph, field


def node(graph, node_id, kind, depends_on):
    graph['nodes'].append({'id': node_id, 'kind': kind, 'label': node_id})
    graph['edges'].append({'from': depends_on, 'to': node_id})


class ImpactTests(unittest.TestCase):
    def test_digest_matches_native_store_and_changes_with_any_schema_revision(self):
        schema, _, _ = fixture()
        self.assertEqual(schema_digest(schema), hashlib.sha256(canonical(schema)).hexdigest())
        self.assertEqual(schema_digest(schema), schema_digest(dict(reversed(list(schema.items())))))
        original = schema_digest(schema)
        schema['entities'][1]['attributes'][1]['length'] = 200
        self.assertNotEqual(schema_digest(schema), original)

    def test_missing_graph_never_fabricates_a_zero(self):
        schema, _, field = fixture()
        result = analyze_impact(schema, 'customers', 'email')
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(result['reason'], 'graph_missing')
        self.assertEqual(result['source_field_id'], field)
        self.assertEqual(result['counts'], dict.fromkeys(['api', 'backend', 'screen', 'test']))
        self.assertEqual(result['counts'], result['observed_counts'])
        self.assertEqual(result['impacted_ids'], [])
        self.assertIsNone(result['suggested_status'])

    def test_four_three_six_eleven_counts_require_twenty_four_actual_nodes(self):
        schema, graph, field = fixture()
        expected = {'api': 4, 'backend': 3, 'screen': 6, 'test': 11}
        for kind, amount in expected.items():
            for index in range(amount):
                node(graph, f'{kind}:{index}', kind, field if kind == 'api' else 'api:0')
        before = copy.deepcopy((schema, graph))
        result = analyze_impact(schema, 'customers', 'email', graph)
        self.assertEqual(result['status'], 'LINKED')
        self.assertEqual(result['counts'], expected)
        self.assertEqual(result['observed_counts'], expected)
        self.assertEqual(len(result['direct']), 4)
        self.assertEqual(len(result['indirect']), 20)
        self.assertEqual(len(set(result['impacted_ids'])), 24)
        self.assertEqual(result['suggested_status'], 'STALE')
        self.assertEqual((schema, graph), before)
        self.assertEqual(result['basis'], 'registered_traceability')
        self.assertTrue(all(item['suggested_status'] == 'STALE' for item in result['direct'] + result['indirect']))

    def test_partial_coverage_never_turns_undeclared_categories_into_verified_zeros(self):
        schema, graph, field = fixture(covered=False)
        node(graph, 'api:contact', 'api', field)
        result = analyze_impact(schema, 'customers', 'email', graph)
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertTrue(all(value is None for value in result['counts'].values()))
        self.assertEqual(result['observed_counts'], {'api': 1, 'backend': 0, 'screen': 0, 'test': 0})
        graph['covered_kinds'] = ['api', 'backend']
        result = analyze_impact(schema, 'customers', 'email', graph)
        self.assertEqual(result['counts'], {'api': 1, 'backend': 0, 'screen': None, 'test': None})
        self.assertEqual(result['covered_kinds'], ['api', 'backend'])

    def test_only_declared_current_empty_graph_can_return_registered_zero_counts(self):
        schema, graph, _ = fixture()
        result = analyze_impact(schema, 'customers', 'email', graph)
        self.assertEqual(result['status'], 'LINKED')
        self.assertEqual(result['counts'], {'api': 0, 'backend': 0, 'screen': 0, 'test': 0})
        self.assertIsNone(result['suggested_status'])
        graph['nodes'].clear()
        result = analyze_impact(schema, 'customers', 'email', graph)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(result['reason'], 'field_node_missing')
        self.assertTrue(all(value is None for value in result['counts'].values()))

    def test_other_field_changes_invalidate_entire_graph_instead_of_reusing_counts(self):
        schema, graph, field = fixture()
        node(graph, 'api:contact', 'api', field)
        schema['entities'][1]['attributes'][1]['length'] = 200
        result = analyze_impact(schema, 'customers', 'email', graph)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(result['reason'], 'schema_revision_mismatch')
        self.assertTrue(all(value is None for value in result['observed_counts'].values()))
        self.assertEqual(result['direct'], [])
        graph['nodes'][0]['id'] = 'field:old_table:old_field'
        graph['edges'][0]['from'] = 'field:old_table:old_field'
        self.assertEqual(analyze_impact(schema, 'customers', 'email', graph)['status'], 'UNKNOWN')

    def test_diamond_cycle_and_self_edge_are_deduplicated_with_stable_shortest_paths(self):
        schema, graph, field = fixture()
        node(graph, 'api:b', 'api', field)
        node(graph, 'api:a', 'api', field)
        node(graph, 'backend:save', 'backend', 'api:b')
        node(graph, 'screen:contact', 'screen', 'backend:save')
        graph['edges'] += [{'from': 'api:a', 'to': 'backend:save'},
                           {'from': 'backend:save', 'to': 'api:a'},
                           {'from': field, 'to': field},
                           {'from': 'screen:contact', 'to': field}]
        result = analyze_impact(schema, 'customers', 'email', graph)
        self.assertEqual(result['counts'], {'api': 2, 'backend': 1, 'screen': 1, 'test': 0})
        target = next(item for item in result['indirect'] if item['id'] == 'backend:save')
        self.assertEqual(target['path'], [field, 'api:a', 'backend:save'])
        graph['edges'].reverse()
        graph['nodes'].reverse()
        graph['covered_kinds'].reverse()
        self.assertEqual(analyze_impact(schema, 'customers', 'email', graph), result)

    def test_direction_and_disconnected_artifacts_do_not_inflate_counts(self):
        schema, graph, field = fixture()
        graph['nodes'].append({'id': 'api:upstream', 'kind': 'api', 'label': 'Upstream'})
        graph['nodes'].append({'id': 'test:unrelated', 'kind': 'test', 'label': 'Unrelated'})
        graph['edges'].append({'from': 'api:upstream', 'to': field})
        self.assertEqual(analyze_impact(schema, 'customers', 'email', graph)['impacted_ids'], [])

    def test_native_field_nodes_can_bridge_dependencies_but_are_not_api_artifacts(self):
        schema, graph, field = fixture()
        other = field_node_id('customers', 'display_name')
        node(graph, other, 'field', field)
        node(graph, 'api:customer', 'api', other)
        result = analyze_impact(schema, 'customers', 'email', graph)
        self.assertEqual(result['counts']['api'], 1)
        self.assertEqual(result['direct'], [])
        self.assertEqual(result['indirect'][0]['path'], [field, other, 'api:customer'])
        self.assertNotIn(other, result['impacted_ids'])

    def test_invalid_native_field_or_schema_has_a_safe_constant_error(self):
        schema, _, _ = fixture()
        for table, field in [('customers', 'absent'), ('../../private', 'email'), (True, 'email')]:
            with self.subTest(table=table, field=field), self.assertRaisesRegex(ValueError, '^invalid_impact_field$'):
                analyze_impact(schema, table, field)
        schema['approved'] = True
        with self.assertRaisesRegex(ValueError, '^invalid_schema$'):
            schema_digest(schema)

    def test_exact_shape_bounds_duplicate_nodes_edges_and_dangling_references(self):
        schema, graph, field = fixture()
        node(graph, 'api:contact', 'api', field)
        invalid = []
        for key, value in [('approved', True), ('format', 'other'), ('schema_digest', 'A' * 64),
                           ('covered_kinds', ['api', 'api']), ('covered_kinds', [True]),
                           ('nodes', graph['nodes'] * 257), ('edges', graph['edges'] * 2049)]:
            invalid.append({**graph, key: value})
        for kind, value in [('nodes', graph['nodes'] * 2), ('edges', graph['edges'] * 2),
                            ('nodes', [None]), ('edges', [{'from': field, 'to': 'api:missing'}])]:
            invalid.append({**graph, kind: value})
        for key, value in [('kind', 'approval'), ('label', '\ud800'), ('label', '\x00'),
                           ('label', 'x' * 301), ('id', 'bad\nnode'), ('extra', True)]:
            variant = copy.deepcopy(graph)
            variant['nodes'][1][key] = value
            invalid.append(variant)
        for value in [None, [], *invalid]:
            with self.subTest(value_type=type(value).__name__), self.assertRaisesRegex(ValueError, '^invalid_impact_graph$'):
                validate_graph(value)
        invalid_field = copy.deepcopy(graph)
        invalid_field['nodes'][0]['id'] = 'field:customers:missing'
        invalid_field['edges'][0]['from'] = 'field:customers:missing'
        with self.assertRaisesRegex(ValueError, '^invalid_impact_graph$'):
            analyze_impact(schema, 'customers', 'email', invalid_field)

    def test_multibyte_graph_bytes_and_total_returned_paths_are_bounded(self):
        schema, graph, field = fixture()
        for index in range(150):
            node(graph, f'api:{index}', 'api', field)
            graph['nodes'][-1]['label'] = '가' * 300
        with self.assertRaisesRegex(ValueError, '^invalid_impact_graph$'):
            validate_graph(graph)
        schema, graph, field = fixture()
        previous = field
        for index in range(150):
            current = f'backend:{index}'
            node(graph, current, 'backend', previous)
            previous = current
        with self.assertRaisesRegex(ValueError, '^impact_graph_too_complex$'):
            analyze_impact(schema, 'customers', 'email', graph)

    def test_validation_and_results_do_not_alias_graph_labels_or_paths(self):
        schema, graph, field = fixture()
        node(graph, 'api:contact', 'api', field)
        validated = validate_graph(graph)
        validated['nodes'][1]['label'] = 'changed'
        result = analyze_impact(schema, 'customers', 'email', graph)
        result['direct'][0]['path'].clear()
        result['direct'][0]['label'] = 'changed-again'
        self.assertEqual(graph['nodes'][1]['label'], 'api:contact')


if __name__ == '__main__':
    unittest.main()
