"""Static contract regressions. Generated app and test source remain inert data."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from channelshift import pipeline_artifacts as artifacts
from channelshift import pipeline_contracts as contracts
from test_pipeline_artifacts import all_artifacts, file, openapi


def replace(values, stage, path, content):
    next(item for item in values[stage]['files'] if item['path'] == path)['content'] = content


def api(values, document):
    replace(values, 'api', 'api/openapi.json', json.dumps(document))


class ContractTests(unittest.TestCase):
    def invalid(self, stage, values):
        with self.assertRaisesRegex(ValueError, '^invalid_pipeline_artifact$'):
            artifacts.validate_stage(stage, values[stage]['files'], values)

    def test_links_route_symbols_and_screen_declarations_are_recorded_without_execution(self):
        values = all_artifacts()
        values['confirmed_requirement_ids'] = ['REQ-001']
        expected = {'operation_id': 'listInquiries', 'method': 'GET', 'path': '/api/inquiries',
                    'requirement_ids': ['REQ-001'], 'fields': ['inquiries.id', 'inquiries.message'],
                    'screen_ids': ['SCREEN-001']}
        with patch('builtins.exec', side_effect=AssertionError('source must not run')), \
                patch('builtins.eval', side_effect=AssertionError('source must not run')):
            api_checks = artifacts.validate_stage('api', values['api']['files'], values)
            backend = artifacts.validate_stage('backend', values['backend']['files'], values)
            frontend = artifacts.validate_stage('frontend', values['frontend']['files'], values)
        self.assertEqual(api_checks['operation_links'], [expected])
        self.assertEqual(backend['route_bindings'], [{'operation_id': 'listInquiries', 'method': 'GET',
            'path': '/api/inquiries', 'handler': 'listInquiries', 'test_file': 'backend/test_app.py',
            'test_symbol': 'test_list'}])
        self.assertEqual(frontend['screen_bindings'], [{'screen_id': 'SCREEN-001',
            'file': 'frontend/index.html', 'operation_ids': ['listInquiries']}])
        for checks in (api_checks, backend, frontend):
            self.assertFalse(checks['application_execution_performed'])
            self.assertFalse(checks['runtime_behavior_verified'])
        self.assertFalse(backend['test_execution_performed'])
        self.assertIs(backend['test_definitions_checked'], True)
        self.assertEqual(backend['test_definitions_count'], 1)
        self.assertEqual(frontend['api_literal_methods_checked'], 1)

    def test_api_extensions_are_required_bounded_unique_and_reference_known_context(self):
        cases = [
            ('x-channelshift-requirement-ids', []),
            ('x-channelshift-requirement-ids', ['REQ-999']),
            ('x-channelshift-requirement-ids', ['REQ-001', 'REQ-001']),
            ('x-channelshift-requirement-ids', ['internal']),
            ('x-channelshift-fields', ['inquiries.missing']),
            ('x-channelshift-fields', ['missing.id']),
            ('x-channelshift-fields', ['inquiries.id'] * 2),
            ('x-channelshift-fields', 'inquiries.id'),
            ('x-channelshift-screens', ['SCREEN-999']),
            ('x-channelshift-screens', ['SCREEN-001'] * 2),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                values, document = all_artifacts(), openapi()
                values['confirmed_requirement_ids'] = ['REQ-001']
                document['paths']['/api/inquiries']['get'][key] = value
                api(values, document)
                self.invalid('api', values)
        for key in ('x-channelshift-requirement-ids', 'x-channelshift-fields', 'x-channelshift-screens'):
            values, document = all_artifacts(), openapi()
            document['paths']['/api/inquiries']['get'].pop(key)
            api(values, document)
            self.invalid('api', values)

    def test_headless_health_and_informational_screens_use_empty_declarations(self):
        values, document = all_artifacts(), openapi()
        operation = document['paths']['/api/inquiries']['get']
        operation['x-channelshift-fields'] = []
        operation['x-channelshift-screens'] = []
        operation.pop('x-channelshift-table')
        api(values, document)
        replace(values, 'frontend', 'frontend/screens.json', json.dumps({'screens': [{
            'screen_id': 'SCREEN-001', 'file': 'frontend/index.html', 'operation_ids': []}]}))
        self.assertEqual(artifacts.validate_stage('api', values['api']['files'], values)
                         ['operation_links'][0]['screen_ids'], [])
        artifacts.validate_stage('frontend', values['frontend']['files'], values)

    def test_wireframe_must_declare_every_confirmed_client_requirement(self):
        values = all_artifacts()
        values['confirmed_requirement_ids'] = ['REQ-001', 'REQ-002']
        self.invalid('wireframe', values)
        screens = {'screens': [{'id': 'SCREEN-001', 'title': '문의', 'path': '/',
                                'requirement_ids': ['REQ-001', 'REQ-002']}]}
        replace(values, 'wireframe', 'wireframe/screens.json', json.dumps(screens))
        checks = artifacts.validate_stage('wireframe', values['wireframe']['files'], values)
        self.assertEqual(checks['requirement_declarations_covered'], ['REQ-001', 'REQ-002'])
        self.assertTrue(checks['requirement_declarations_checked'])
        self.assertFalse(checks['requirement_coverage_verified'])

    def test_schema_subset_rejects_malformed_nested_shapes_required_items_and_refs(self):
        bad = [
            {'type': 'nonsense'}, {'type': []}, {'type': ['string', 'string']},
            {'type': 'object', 'properties': []},
            {'type': 'object', 'properties': {'id': {'type': 'integer'}}, 'required': ['missing']},
            {'type': 'array'}, {'type': 'array', 'items': 'string'},
            {'type': 'string', 'properties': {}}, {'type': 'number', 'enum': [True]},
            {'type': 'string', 'enum': ['a', 'a']}, {'type': 'integer', 'const': '1'},
            {'type': 'string', 'minLength': 5, 'maxLength': 2},
            {'type': 'number', 'minimum': 10, 'maximum': 5},
            {'type': 'object', 'additionalProperties': 1},
            {'type': 'string', 'unknownKeyword': True},
            {'$ref': '#/components/schemas/Inquiry'},
        ]
        for definition in bad:
            with self.subTest(definition=definition):
                values, document = all_artifacts(), openapi()
                document['components']['schemas']['Inquiry'] = definition
                api(values, document)
                self.invalid('api', values)

    def test_request_response_content_and_parameter_schemas_are_checked(self):
        for body in ({}, {'content': {}}, {'required': 'yes', 'content': {'application/json': {'schema': {'type': 'string'}}}},
                     {'content': {'text/html': {'schema': {'type': 'string'}}}},
                     {'content': {'application/json': {'example': {}}}}):
            values, document = all_artifacts(), openapi()
            document['paths']['/api/inquiries']['get']['requestBody'] = body
            api(values, document)
            self.invalid('api', values)
        for parameter in ({'name': 'limit', 'in': 'query'},
                          {'name': 'limit', 'in': 'query', 'schema': {'type': 'bad'}}):
            values, document = all_artifacts(), openapi()
            document['paths']['/api/inquiries']['get']['parameters'] = [parameter]
            api(values, document)
            self.invalid('api', values)
        values, document = all_artifacts(), openapi()
        document['paths']['/api/inquiries']['get']['requestBody'] = {'content': {'application/json': {'schema': {
            'type': 'object', 'properties': {'message': {'type': 'string', 'minLength': 1}},
            'required': ['message'], 'additionalProperties': False}}}}
        document['paths']['/api/inquiries']['get']['parameters'] = [
            {'name': 'limit', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 1, 'maximum': 100}}]
        api(values, document)
        self.assertGreater(artifacts.validate_stage('api', values['api']['files'], values)['schema_definitions_checked'], 3)

    def test_legacy_import_only_backend_and_missing_manifests_fail(self):
        values = all_artifacts()
        replace(values, 'backend', 'backend/app.py', 'from http.server import HTTPServer\n# Not an implementation\n')
        self.invalid('backend', values)
        for stage, path in (('backend', 'backend/routes.json'), ('frontend', 'frontend/screens.json')):
            values = all_artifacts()
            values[stage]['files'] = [item for item in values[stage]['files'] if item['path'] != path]
            self.invalid(stage, values)

    def test_backend_route_table_requires_exact_methods_paths_handlers_and_literal_registration(self):
        sources = [
            "def listInquiries():\n    return []\n# ROUTES = {('GET','/api/inquiries'):listInquiries}\n",
            "def listInquiries():\n    return []\nROUTES = {('POST','/api/inquiries'):listInquiries}\n",
            "def listInquiries():\n    return []\nROUTES = {('GET','/api/missing'):listInquiries}\n",
            "def listInquiries():\n    return []\nROUTES = {('GET','/api/inquiries'):missing}\n",
            "def listInquiries():\n    return []\nROUTES = dict()\n",
            "def listInquiries():\n    return []\nROUTES = {('GET','/api/inquiries'):listInquiries,('GET','/extra'):listInquiries}\n",
            "def listInquiries():\n    return []\nROUTES = {('GET','/api/inquiries'):listInquiries,('GET','/api/inquiries'):listInquiries}\n",
            "def listInquiries():\n    return []\nROUTES = {}\nROUTES = {('GET','/api/inquiries'):listInquiries}\n",
        ]
        for source in sources:
            values = all_artifacts()
            replace(values, 'backend', 'backend/app.py', source)
            self.invalid('backend', values)

    def test_backend_placeholder_handlers_and_assertionless_or_nested_tests_fail(self):
        for body in ('pass', '...', 'return', 'return None', 'raise NotImplementedError()', '"docstring"'):
            values = all_artifacts()
            replace(values, 'backend', 'backend/app.py', 'def listInquiries():\n    ' + body + '\n'
                    'ROUTES = {("GET", "/api/inquiries"): listInquiries}\n')
            self.invalid('backend', values)
        for source in ('def test_list():\n    pass\n',
                       'def test_list():\n    "assert True"\n',
                       'def test_list():\n    def nested():\n        assert True\n',
                       'def different():\n    assert True\n'):
            values = all_artifacts()
            replace(values, 'backend', 'backend/test_app.py', source)
            self.invalid('backend', values)

    def test_backend_test_class_and_function_symbols_are_inspected_as_source(self):
        values = all_artifacts()
        replace(values, 'backend', 'backend/test_app.py', 'import unittest\n'
                'class ApiTests(unittest.TestCase):\n    def test_list(self):\n        self.assertEqual([], [])\n')
        manifest = {'routes': [{'operation_id': 'listInquiries', 'handler': 'listInquiries',
            'test_file': 'backend/test_app.py', 'test_symbol': 'ApiTests.test_list'}]}
        replace(values, 'backend', 'backend/routes.json', json.dumps(manifest))
        self.assertIs(artifacts.validate_stage('backend', values['backend']['files'], values)['test_definitions_checked'], True)
        for field, bad in (('operation_id', 'missing'), ('handler', 'missing'), ('test_symbol', 'ApiTests.missing'),
                           ('test_file', 'backend/app.py'), ('test_file', '../test_app.py')):
            changed = deepcopy(manifest)
            changed['routes'][0][field] = bad
            replace(values, 'backend', 'backend/routes.json', json.dumps(changed))
            self.invalid('backend', values)
        manifest['routes'] *= 2
        replace(values, 'backend', 'backend/routes.json', json.dumps(manifest))
        self.invalid('backend', values)

    def test_frontend_screen_mapping_must_be_exact_and_match_api_pairs(self):
        variants = [[], [{'screen_id': 'SCREEN-999', 'file': 'frontend/index.html', 'operation_ids': ['listInquiries']}],
            [{'screen_id': 'SCREEN-001', 'file': 'frontend/missing.html', 'operation_ids': ['listInquiries']}],
            [{'screen_id': 'SCREEN-001', 'file': 'frontend/app.js', 'operation_ids': ['listInquiries']}],
            [{'screen_id': 'SCREEN-001', 'file': 'frontend/index.html', 'operation_ids': []}],
            [{'screen_id': 'SCREEN-001', 'file': 'frontend/index.html', 'operation_ids': ['missing']}],
            [{'screen_id': 'SCREEN-001', 'file': 'frontend/index.html', 'operation_ids': ['listInquiries', 'listInquiries']}],
        ]
        variants.append(variants[4] * 2)
        for rows in variants:
            values = all_artifacts()
            replace(values, 'frontend', 'frontend/screens.json', json.dumps({'screens': rows}))
            self.invalid('frontend', values)
        values, document = all_artifacts(), openapi()
        document['paths']['/api/inquiries']['get']['x-channelshift-screens'] = []
        api(values, document)
        self.invalid('frontend', values)

    def test_literal_fetch_methods_are_compared_with_api_including_default_get(self):
        for source in ("fetch('/api/inquiries', {method:'POST'})", "fetch('/api/inquiries', {'method':'DELETE'})",
                       "fetch('/api/inquiries', {method:'INVALID'})"):
            values = all_artifacts()
            replace(values, 'frontend', 'frontend/app.js', source)
            self.invalid('frontend', values)
        for source in ("fetch('/api/inquiries', {method:'get'})", "fetch('/api/inquiries')",
                       "fetch('/api/inquiries', {headers: {'X-Example': '}'}, method: 'GET'})"):
            values = all_artifacts()
            replace(values, 'frontend', 'frontend/app.js', source)
            self.assertEqual(artifacts.validate_stage('frontend', values['frontend']['files'], values)['api_literal_methods_checked'], 1)

    def test_comments_strings_regex_and_html_attributes_do_not_supply_fetch_evidence(self):
        source = '''// fetch('/api/missing')
        /* fetch('/api/missing', {method:'POST'}) */
        const example = "fetch('/api/missing')";
        const pattern = /fetch\\('missing'\\)/;
        const makePattern = () => /fetch('missing')/;
        fetch('/api/inquiries');'''
        values = all_artifacts()
        replace(values, 'frontend', 'frontend/app.js', source)
        values['frontend']['files'][0]['content'] = values['frontend']['files'][0]['content'].replace(
            '<h1>테스트</h1>', '<h1 title="fetch(\'/api/missing\')">테스트</h1><!-- fetch(\'/api/missing\') -->')
        checks = artifacts.validate_stage('frontend', values['frontend']['files'], values)
        self.assertEqual(checks['fetch_calls_detected'], 1)
        self.assertEqual(checks['api_literal_methods_checked'], 1)

    def test_dynamic_urls_and_methods_stay_explicitly_unchecked(self):
        source = "fetch(url); fetch(`/api/${id}`); fetch('/api/inquiries', options); " \
                 "fetch('/api/inquiries', {method: method}); fetch('/api/inquiries', {...options});"
        values = all_artifacts()
        replace(values, 'frontend', 'frontend/app.js', source)
        checks = artifacts.validate_stage('frontend', values['frontend']['files'], values)
        self.assertEqual(checks['fetch_calls_detected'], 5)
        self.assertEqual(checks['dynamic_fetch_calls_not_checked'], 5)
        self.assertEqual(checks['api_literal_methods_checked'], 0)

    def test_inline_script_extraction_ignores_json_blocks_and_html_comments(self):
        self.assertEqual(contracts.inline_scripts('<!-- <script>fetch("bad")</script> -->'
            '<script type="application/json">{"text":"fetch(bad)"}</script>'
            '<script type="module">fetch("/api/inquiries")</script>'), ['fetch("/api/inquiries")'])

    def test_options_expression_after_literal_object_is_not_a_verified_method(self):
        for options in ("{method:'GET'} && {method:'POST'}", "{method:'GET'} || fallback",
                        "{method:'GET'}.options", "{method:'GET'} ? first : second",
                        "{__proto__: {method:'POST'}}", "{'__proto__': inheritedOptions}"):
            source = "fetch('/api/inquiries', " + options + ");"
            self.assertEqual(list(contracts.literal_fetches(source)), [('/api/inquiries', None)])
            values = all_artifacts()
            replace(values, 'frontend', 'frontend/app.js', source)
            checks = artifacts.validate_stage('frontend', values['frontend']['files'], values)
            self.assertEqual(checks['api_literal_methods_checked'], 0)
            self.assertEqual(checks['dynamic_fetch_calls_not_checked'], 1)


if __name__ == '__main__':
    unittest.main()
