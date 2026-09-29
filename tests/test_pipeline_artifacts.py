"""Bounded static artifact checks and real isolated SQLite schema verification."""
from copy import deepcopy
import io
import json
import sqlite3
import unittest
from unittest.mock import patch
import zipfile

from channelshift import pipeline_artifacts as artifacts


def file(path, content):
    return {'path': path, 'content': content}


def schema():
    return {'format': 'channelshift.schema/v1', 'name': '테스트 프로젝트', 'database': 'sqlite',
            'entities': [{'name': 'inquiries', 'attributes': [
                {'name': 'id', 'type': 'integer', 'nullable': False, 'primary_key': True},
                {'name': 'message', 'type': 'text', 'nullable': False}]}], 'relations': []}


def openapi():
    return {'openapi': '3.1.0', 'info': {'title': '테스트 API', 'version': '1.0.0'},
            'paths': {'/api/inquiries': {'get': {'operationId': 'listInquiries',
                'x-channelshift-requirement-ids': ['REQ-001'],
                'x-channelshift-fields': ['inquiries.id', 'inquiries.message'],
                'x-channelshift-screens': ['SCREEN-001'],
                'x-channelshift-table': 'inquiries', 'responses': {'200': {
                    'description': '조회 성공', 'content': {'application/json': {
                        'schema': {'$ref': '#/components/schemas/Inquiry'}}}}}}}},
            'components': {'schemas': {'Inquiry': {'type': 'object',
                'properties': {'id': {'type': 'integer'}, 'message': {'type': 'string'}}}}}}


def frontend():
    return [file('frontend/index.html', '<!doctype html><html><head>'
                 '<link rel="stylesheet" href="/style.css"></head><body><h1>테스트</h1>'
                 '<a href="/privacy.html">개인정보처리방침</a>'
                 '<a href="/terms.html">서비스이용약관</a>'
                 '<a href="/refund.html">취소환불규정</a>'
                 '<a href="/contact.html">고객문의</a>'
                 '<!-- CHANNELSHIFT_SITE_FOOTER -->'
                 '<script src="/app.js"></script></body></html>'),
            file('frontend/app.js', "fetch('/api/inquiries').then(response => response.json());"),
            file('frontend/style.css', 'body { color: #111; }'),
            file('frontend/screens.json', json.dumps({'screens': [{'screen_id': 'SCREEN-001',
                'file': 'frontend/index.html', 'operation_ids': ['listInquiries']}]}))]


def all_artifacts():
    erd = {'files': [file('erd/schema.json', json.dumps(schema()))]}
    return {
        'wireframe': {'files': [file('wireframe/index.html', '<html><body>문의</body></html>'),
            file('wireframe/screens.json', json.dumps({'screens': [
                {'id': 'SCREEN-001', 'title': '문의 화면', 'path': '/', 'requirement_ids': ['REQ-001']}]}))]},
        'erd': erd,
        'database': artifacts.build_database(schema()),
        'api': {'files': [file('api/openapi.json', json.dumps(openapi()))]},
        'backend': {'files': [file('backend/app.py', 'raise RuntimeError("must never run")\n'
                                  'def listInquiries():\n    return []\n'
                                  'ROUTES = {("GET", "/api/inquiries"): listInquiries}\n'),
                             file('backend/README.md', 'Manual launch: python backend/app.py\n'),
                             file('backend/test_app.py', 'from app import listInquiries\n'
                                  'def test_list():\n    assert listInquiries() == []\n'),
                             file('backend/routes.json', json.dumps({'routes': [{
                                 'operation_id': 'listInquiries', 'handler': 'listInquiries',
                                 'test_file': 'backend/test_app.py', 'test_symbol': 'test_list'}]}))]},
        'frontend': {'files': frontend()},
    }


def obligations():
    return {'company': {'name': '테스트 회사', 'representative': '테스트 대표',
                        'business_number': '000-00-00000', 'address': '테스트 주소'},
            'commerce': {'registration_number': '테스트 신고번호'},
            'contact': {'email': 'test@example.invalid', 'phone': '010-0000-0000'},
            'hosting': {'name': '테스트 호스팅'},
            'policies': {'privacy': '테스트 개인정보 문구', 'terms': '테스트 약관 문구',
                         'refund': '테스트 환불 문구'}}


class PipelineArtifactTests(unittest.TestCase):
    def test_database_uses_only_fresh_memory_connections_and_real_checks(self):
        original = sqlite3.connect
        with patch.object(artifacts.sqlite3, 'connect', wraps=original) as connect:
            result = artifacts.build_database(schema())
        connect.assert_called_once_with(':memory:')
        self.assertEqual([f['path'] for f in result['files']], ['database/schema.sql', 'database/checks.json'])
        checks = result['checks']
        self.assertTrue(checks['sqlite_executed'])
        self.assertTrue(checks['foreign_keys_enabled'])
        self.assertEqual(checks['foreign_key_violations'], 0)
        self.assertEqual(checks['integrity_check'], 'ok')
        self.assertEqual(checks['tables'], ['inquiries'])
        self.assertFalse(checks['persistent_database_created'])
        self.assertFalse(checks['application_executed'])
        self.assertEqual(json.loads(result['files'][1]['content']), checks)

    def test_database_dialect_and_invalid_schema_rejected(self):
        wrong_dialect = schema()
        wrong_dialect['database'] = 'postgresql'
        with self.assertRaisesRegex(ValueError, '^pipeline_sqlite_required$'):
            artifacts.build_database(wrong_dialect)
        malformed = schema()
        malformed['entities'][0]['name'] = 'bad; SQL'
        with self.assertRaisesRegex(ValueError, '^invalid_schema$'):
            artifacts.build_database(malformed)

    def test_database_sql_syntax_or_verification_failure_does_not_return_success(self):
        for sql in ('CREATE TABLE', 'CREATE TABLE wrong_table (id INTEGER);'):
            with self.subTest(sql=sql):
                with patch.object(artifacts, 'export_sql', return_value=sql):
                    with self.assertRaisesRegex(ValueError, '^pipeline_database_check_failed$'):
                        artifacts.build_database(schema())

    def test_database_literal_quotes_and_sqlite_like_table_names_remain_data(self):
        value = schema()
        value['entities'][0]['name'] = 'sqlitea'
        value['entities'][0]['attributes'][1]['default'] = "'); DROP TABLE sqlitea; --"
        before = deepcopy(value)
        self.assertEqual(artifacts.build_database(value)['checks']['tables'], ['sqlitea'])
        self.assertEqual(value, before)

    def test_path_traversal_devices_absolute_and_case_collisions_rejected(self):
        bad_paths = ['../app.py', '/backend/app.py', 'C:/backend/app.py', 'backend\\app.py',
                     'backend/../app.py', 'backend/CON.txt', 'backend/NUL',
                     'backend/.git/config', 'backend/app.py.', 'backend/app.py:stream',
                     'backend/%2e%2e/app.py', 'backend/ spaced.py']
        for path in bad_paths:
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, '^invalid_pipeline_artifact$'):
                    artifacts.validate_stage('backend', [file(path, 'pass')], {})
        for extras in ([file('backend/APP.py', 'pass')],
                       [file('backend/app.py/child.py', 'pass')]):
            with self.assertRaisesRegex(ValueError, '^invalid_pipeline_artifact$'):
                artifacts.validate_stage('backend', all_artifacts()['backend']['files'] + extras, {})

    def test_bounded_file_shapes_and_contents(self):
        for value in (None, [], [file('backend/app.py', '\x00')],
                      [file('backend/app.py', '\ud800')],
                      [file('backend/app.py', 'x' * (artifacts.MAX_FILE_BYTES + 1))],
                      [{'path': 'backend/app.py', 'content': 'pass', 'execute': True}]):
            with self.subTest(shape=type(value).__name__):
                with self.assertRaisesRegex(ValueError, '^invalid_pipeline_artifact$'):
                    artifacts.validate_stage('backend', value, {})

    def test_wireframe_screen_shape_duplicates_and_html_document(self):
        files = all_artifacts()['wireframe']['files']
        checks = artifacts.validate_stage('wireframe', files, {})
        self.assertEqual(checks['screens_count'], 1)
        self.assertFalse(checks['requirement_coverage_verified'])
        duplicate = deepcopy(files)
        screens = json.loads(duplicate[1]['content'])
        screens['screens'] *= 2
        duplicate[1]['content'] = json.dumps(screens)
        with self.assertRaisesRegex(ValueError, '^invalid_pipeline_artifact$'):
            artifacts.validate_stage('wireframe', duplicate, {})
        invalid = deepcopy(files)
        invalid[0]['content'] = '<div>Not a complete page</div>'
        with self.assertRaisesRegex(ValueError, '^invalid_pipeline_artifact$'):
            artifacts.validate_stage('wireframe', invalid, {})

    def test_wireframe_known_client_ids_checked_when_context_supplied(self):
        files = all_artifacts()['wireframe']['files']
        checks = artifacts.validate_stage('wireframe', files, {'confirmed_requirement_ids': ['REQ-001']})
        self.assertTrue(checks['requirement_id_membership_checked'])
        self.assertFalse(checks['requirement_coverage_verified'])
        for ids in (['REQ-999'], [], ['REQ-001', 'REQ-001'], ['not-an-id']):
            with self.subTest(ids=ids):
                with self.assertRaisesRegex(ValueError, '^invalid_pipeline_artifact$'):
                    artifacts.validate_stage('wireframe', files, {'confirmed_requirement_ids': ids})

    def test_json_duplicate_nonfinite_and_excessive_nesting_rejected(self):
        for content in ('{"screens":[],"screens":[]}', '{"screens":NaN}', '[' * 30 + '0' + ']' * 30):
            files = [file('wireframe/index.html', '<html><body>Test</body></html>'),
                     file('wireframe/screens.json', content)]
            with self.subTest(content=content):
                with self.assertRaisesRegex(ValueError, '^invalid_pipeline_artifact$'):
                    artifacts.validate_stage('wireframe', files, {})

    def test_api_local_refs_operations_and_declared_database_links_checked(self):
        deps = all_artifacts()
        checks = artifacts.validate_stage('api', deps['api']['files'], deps)
        self.assertEqual(checks['operations'], ['GET /api/inquiries'])
        self.assertEqual(checks['local_references_checked'], 1)
        self.assertEqual(checks['database_links_checked'], 1)
        self.assertFalse(checks['full_openapi_validation_performed'])
        self.assertFalse(checks['application_execution_performed'])

    def test_api_broken_or_remote_refs_and_missing_tables_rejected(self):
        for ref in ('https://example.invalid/schema.json', '#/components/schemas/Missing'):
            data = openapi()
            data['paths']['/api/inquiries']['get']['responses']['200']['content']['application/json']['schema']['$ref'] = ref
            with self.subTest(ref=ref):
                with self.assertRaisesRegex(ValueError, '^invalid_pipeline_artifact$'):
                    artifacts.validate_stage('api', [file('api/openapi.json', json.dumps(data))], all_artifacts())
        data = openapi()
        data['paths']['/api/inquiries']['get']['x-channelshift-table'] = 'missing'
        with self.assertRaisesRegex(ValueError, '^invalid_pipeline_artifact$'):
            artifacts.validate_stage('api', [file('api/openapi.json', json.dumps(data))], all_artifacts())

    def test_api_duplicate_ids_bad_responses_and_missing_path_parameter_rejected(self):
        changes = []
        data = openapi()
        data['paths']['/api/other'] = deepcopy(data['paths']['/api/inquiries'])
        changes.append(data)
        data = openapi()
        data['paths']['/api/inquiries']['get']['responses'] = {}
        changes.append(data)
        data = openapi()
        data['paths']['/api/inquiries/{id}'] = data['paths'].pop('/api/inquiries')
        changes.append(data)
        for data in changes:
            with self.assertRaisesRegex(ValueError, '^invalid_pipeline_artifact$'):
                artifacts.validate_stage('api', [file('api/openapi.json', json.dumps(data))], all_artifacts())

    def test_backend_parsed_but_never_imported_or_executed(self):
        files = all_artifacts()['backend']['files']
        checks = artifacts.validate_stage('backend', files, all_artifacts())
        self.assertEqual(checks['python_files_parsed'], 2)
        self.assertFalse(checks['imports_executed'])
        self.assertFalse(checks['runtime_behavior_verified'])
        files[0]['content'] = 'def broken(:\n'
        with self.assertRaisesRegex(ValueError, '^invalid_pipeline_artifact$'):
            artifacts.validate_stage('backend', files, all_artifacts())
        files[0]['content'] = 'type Alias = int\n'
        with self.assertRaisesRegex(ValueError, '^invalid_pipeline_artifact$'):
            artifacts.validate_stage('backend', files, all_artifacts())

    def test_frontend_links_and_literal_api_paths_checked_without_js_execution(self):
        deps = all_artifacts()
        files = frontend()
        files[1]['content'] += "\nthrow new Error('not run');\nfetch(dynamicUrl);"
        checks = artifacts.validate_stage('frontend', files, deps)
        self.assertEqual(checks['html_pages_parsed'], 1)
        self.assertEqual(checks['api_literal_paths_checked'], 1)
        self.assertEqual(checks['dynamic_fetch_calls_not_checked'], 1)
        self.assertFalse(checks['javascript_syntax_validated'])
        self.assertFalse(checks['application_execution_performed'])

    def test_frontend_missing_asset_or_unknown_api_rejected(self):
        for target in ('/missing.js', '/api/missing', '../escape.html', 'javascript:alert(1)'):
            files = frontend()
            files[0]['content'] = files[0]['content'].replace('src="/app.js"', 'src="' + target + '"')
            with self.subTest(target=target):
                with self.assertRaisesRegex(ValueError, '^invalid_pipeline_artifact$'):
                    artifacts.validate_stage('frontend', files, all_artifacts())
        files = frontend()
        files[1]['content'] = "fetch('/api/missing');"
        with self.assertRaisesRegex(ValueError, '^invalid_pipeline_artifact$'):
            artifacts.validate_stage('frontend', files, all_artifacts())

    def test_frontend_required_assets_must_be_connected_as_script_and_stylesheet(self):
        for removed in ('<script src="/app.js"></script>', '<link rel="stylesheet" href="/style.css">'):
            files = frontend()
            files[0]['content'] = files[0]['content'].replace(removed, '')
            with self.subTest(removed=removed):
                with self.assertRaisesRegex(ValueError, '^invalid_pipeline_artifact$'):
                    artifacts.validate_stage('frontend', files, all_artifacts())
        files = frontend()
        files[0]['content'] = files[0]['content'].replace('src="/app.js"', 'src="./app.js?v=1"')
        files[0]['content'] = files[0]['content'].replace('href="/style.css"', 'href="style.css?v=1"')
        artifacts.validate_stage('frontend', files, all_artifacts())

    def test_frontend_real_marker_and_links_required_and_policy_override_refused(self):
        for bad in ('', artifacts.FOOTER_MARKER * 2,
                    '<script>const fake = "' + artifacts.FOOTER_MARKER + '";</script>'):
            files = frontend()
            files[0]['content'] = files[0]['content'].replace(artifacts.FOOTER_MARKER, bad)
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, '^invalid_pipeline_artifact$'):
                    artifacts.validate_stage('frontend', files, all_artifacts())
        files = frontend()
        files[0]['content'] = files[0]['content'].replace('<a href="/refund.html">취소환불규정</a>',
                                                        '<!-- href="/refund.html" -->')
        with self.assertRaisesRegex(ValueError, '^invalid_pipeline_artifact$'):
            artifacts.validate_stage('frontend', files, all_artifacts())
        for path in ('frontend/privacy.html', 'frontend/Privacy.HTML', 'frontend/footer.html'):
            with self.assertRaisesRegex(ValueError, '^invalid_pipeline_artifact$'):
                artifacts.validate_stage('frontend', frontend() + [file(path, '<html><body>Fake</body></html>')], all_artifacts())

    def test_database_artifact_must_match_recomputed_erd_sql_and_checks(self):
        deps = all_artifacts()
        checks = artifacts.validate_stage('database', deps['database']['files'], deps)
        self.assertTrue(checks['sqlite_executed'])
        altered = deepcopy(deps['database']['files'])
        altered[0]['content'] += 'DROP TABLE inquiries;\n'
        with self.assertRaisesRegex(ValueError, '^invalid_pipeline_artifact$'):
            artifacts.validate_stage('database', altered, deps)

    def test_bundle_is_deterministic_and_preserves_actual_file_digests(self):
        values = all_artifacts()
        for value in values.values():
            value['digest'] = 'a' * 64
        original = deepcopy(values)
        first = artifacts.bundle('테스트 사이트', values, obligations())
        reverse = {key: dict(value, files=list(reversed(value['files'])))
                   for key, value in reversed(list(values.items()))}
        self.assertEqual(first, artifacts.bundle('테스트 사이트', reverse, obligations()))
        self.assertEqual(values, original)
        with zipfile.ZipFile(io.BytesIO(first)) as archive:
            self.assertEqual(archive.namelist(), sorted(archive.namelist()))
            manifest = json.loads(archive.read('manifest.json'))
            self.assertFalse(manifest['application_execution_performed'])
            self.assertFalse(manifest['deployment_performed'])
            self.assertTrue(manifest['legal_review_required'])
            self.assertEqual({entry['path'] for entry in manifest['export_files']},
                             set(archive.namelist()) - {'manifest.json'})
            self.assertEqual(manifest['artifacts']['backend']['source_digest'], 'a' * 64)
            self.assertNotEqual(manifest['artifacts']['backend']['files_digest'], 'a' * 64)
            self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()))
            self.assertIn(b'python backend/app.py', archive.read('README.md'))

    def test_bundle_injects_real_policy_footer_into_every_frontend_html(self):
        values = all_artifacts()
        values['frontend']['files'].append(file('frontend/about.html', '<html><body>소개</body></html>'))
        values['frontend']['files'].append(file('frontend/FAQ.HTML', '<html><body>질문</body></html>'))
        with zipfile.ZipFile(io.BytesIO(artifacts.bundle('테스트', values, obligations()))) as archive:
            for path in archive.namelist():
                if path.startswith('frontend/') and path.lower().endswith('.html'):
                    content = archive.read(path).decode()
                    self.assertEqual(content.count('<footer aria-label="사이트 필수 정보">'), 1)
                    self.assertIn('테스트 회사', content)
                    self.assertIn('/refund.html', content)
                    self.assertNotIn(artifacts.FOOTER_MARKER, content)
            self.assertIn('테스트 환불 문구', archive.read('frontend/refund.html').decode())

    def test_bundle_requires_full_stage_set_and_complete_obligations(self):
        values = all_artifacts()
        del values['api']
        with self.assertRaisesRegex(ValueError, '^invalid_pipeline_artifact$'):
            artifacts.bundle('테스트', values, obligations())
        blanks = obligations()
        blanks['hosting']['name'] = ''
        with self.assertRaisesRegex(ValueError, '^site_obligations_incomplete$'):
            artifacts.bundle('테스트', all_artifacts(), blanks)

    def test_bundle_accepts_200_character_project_name_and_rejects_201(self):
        name = '가' * 200
        data = artifacts.bundle(name, all_artifacts(), obligations())
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(json.loads(archive.read('manifest.json'))['name'], name)
            self.assertIn(name, archive.read('frontend/privacy.html').decode())
        with self.assertRaisesRegex(ValueError, '^invalid_pipeline_artifact$'):
            artifacts.bundle('가' * 201, all_artifacts(), obligations())

    def test_bundle_optional_evidence_is_complete_hashed_and_deterministic(self):
        evidence = {'requirements': {'candidate': {'requirements': [{'id': 'REQ-001'}]}},
                    'reviews': {'frontend': {'note': '작업자가 검토함', 'digest': 'a' * 64}},
                    'delivery': {'application_executed': False}}
        original = deepcopy(evidence)
        data = artifacts.bundle('테스트', all_artifacts(), obligations(), evidence=evidence)
        self.assertEqual(data, artifacts.bundle('테스트', all_artifacts(), obligations(), evidence=evidence))
        self.assertEqual(original, evidence)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            for key, value in evidence.items():
                path = 'evidence/' + key + '.json'
                self.assertEqual(json.loads(archive.read(path)), value)
                self.assertIn(path, [item['path'] for item in manifest['export_files']])

    def test_bundle_evidence_malformed_excessive_and_nonfinite_rejected(self):
        cases = [{}, {'requirements': {}, 'reviews': {}, 'delivery': []},
                 {'requirements': {'n': float('nan')}, 'reviews': {}, 'delivery': {}},
                 {'requirements': {'text': '가' * 200_000}, 'reviews': {}, 'delivery': {}}]
        cyclic = {'requirements': {}, 'reviews': {}, 'delivery': {}}
        cyclic['requirements']['cycle'] = cyclic
        cases.append(cyclic)
        for evidence in cases:
            with self.assertRaisesRegex(ValueError, '^invalid_pipeline_artifact$'):
                artifacts.bundle('테스트', all_artifacts(), obligations(), evidence=evidence)


if __name__ == '__main__':
    unittest.main()
