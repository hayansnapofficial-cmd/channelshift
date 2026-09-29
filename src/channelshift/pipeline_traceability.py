"""Derive field impact links from current, server-validated artifact contracts.

These are declared source relationships, not execution evidence or a proof that
all code dependencies were discovered. Missing fields/categories stay unknown.
No artifacts, approvals or schema versions are changed by this projection.
"""
from __future__ import annotations

import hashlib
import json

from .impact import GRAPH_FORMAT, MAX_NODES, MAX_EDGES, MAX_GRAPH_BYTES, field_node_id, schema_digest, validate_graph


def _id(kind, value):
    return kind + ':' + hashlib.sha256(value.encode('utf-8')).hexdigest()[:32]


def build_traceability(view):
    pipeline = view['pipeline']
    stages = {stage['id']: stage for stage in pipeline['stages']}
    current = {key: stage['artifact'] for key, stage in stages.items()
               if stage['state'] in {'generated', 'approved'} and stage.get('artifact')}
    result = {'ok': True, 'graph': None, 'reason': 'erd_not_current',
              'pipeline_revision': pipeline['revision'], 'basis': 'validated_contract_declarations',
              'artifact_digests': {key: artifact['digest'] for key, artifact in current.items()},
              'runtime_behavior_verified': False}
    if 'erd' not in current:
        return result
    try:
        model = json.loads(next(file['content'] for file in current['erd']['files']
                                if file['path'] == 'erd/schema.json'))
        digest = schema_digest(model)
    except (ValueError, StopIteration, KeyError, TypeError):
        return result
    api = current.get('api', {}).get('checks', {})
    if api.get('operation_link_declarations_checked') is not True:
        result['reason'] = 'contract_incomplete'
        return result
    graph = {'format': GRAPH_FORMAT, 'schema_digest': digest,
             'nodes': [], 'edges': [], 'covered_kinds': ['api']}
    nodes, edges = {}, set()
    fields = {entity['name'] + '.' + attribute['name'] for entity in model['entities']
              for attribute in entity['attributes']}

    def node(key, kind, label):
        if key not in nodes:
            nodes[key] = {'id': key, 'kind': kind, 'label': label[:300]}
        if len(nodes) > MAX_NODES:
            raise ValueError('traceability_limit')
        return key

    def edge(source, target):
        edges.add((source, target))
        if len(edges) > MAX_EDGES:
            raise ValueError('traceability_limit')

    try:
        operations = {}
        for operation in api['operation_links']:
            operation_id = operation['operation_id']
            key = node(_id('api', operation_id), 'api', operation['method'] + ' ' + operation['path'])
            operations[operation_id] = key
            for field in operation['fields']:
                if field not in fields:
                    raise ValueError('contract_incomplete')
                table, attribute = field.split('.')
                source = node(field_node_id(table, attribute), 'field', field)
                edge(source, key)

        backend = current.get('backend', {}).get('checks', {})
        if backend.get('route_declarations_checked') is True and backend.get('test_definitions_checked') is True:
            for binding in backend['route_bindings']:
                source = operations[binding['operation_id']]
                handler = node(_id('backend', binding['handler']), 'backend', 'backend/app.py · ' + binding['handler'])
                edge(source, handler)
                identity = binding['test_file'] + ':' + binding['test_symbol']
                test = node(_id('test', identity), 'test', identity)
                edge(handler, test)
            graph['covered_kinds'].extend(['backend', 'test'])

        frontend = current.get('frontend', {}).get('checks', {})
        if frontend.get('screen_declarations_checked') is True:
            for binding in frontend['screen_bindings']:
                screen = node(_id('screen', binding['screen_id']), 'screen',
                              binding['screen_id'] + ' · ' + binding['file'])
                for operation_id in binding['operation_ids']:
                    edge(operations[operation_id], screen)
            graph['covered_kinds'].append('screen')
        graph['nodes'] = sorted(nodes.values(), key=lambda value: value['id'])
        graph['edges'] = [{'from': source, 'to': target} for source, target in sorted(edges)]
        graph['covered_kinds'].sort()
        if len(json.dumps(graph, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > MAX_GRAPH_BYTES:
            result['reason'] = 'traceability_limit'
            return result
        result['graph'] = validate_graph(graph)
        result['reason'] = 'current' if len(graph['covered_kinds']) == 4 else 'contract_incomplete'
    except (ValueError, KeyError, TypeError):
        # Size and malformed legacy evidence never masquerade as zero impact.
        result['reason'] = 'traceability_limit' if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES else 'contract_incomplete'
        result['graph'] = None
    return result
