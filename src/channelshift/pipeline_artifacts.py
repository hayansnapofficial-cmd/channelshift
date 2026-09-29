"""Bounded artifact checks and deterministic export, without running generated apps.

Only SQL produced by core.export_sql from a validated SQLite schema executes,
on a new in-memory connection. Application files are parsed or packaged as data.
The caller owns approval gates; this module does not infer or grant approvals.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from html.parser import HTMLParser
import io
import json
import posixpath
import re
import sqlite3
from urllib.parse import unquote, urlsplit
import zipfile

from .core import export_sql, validate_schema
from . import pipeline_contracts as contracts
from .site_obligations import render_pages


STAGES = ('wireframe', 'erd', 'database', 'api', 'backend', 'frontend')
FOOTER_MARKER = '<!-- CHANNELSHIFT_SITE_FOOTER -->'
MAX_FILES = 64
MAX_FILE_BYTES = 512 * 1024
MAX_STAGE_BYTES = 2 * 1024 * 1024
MAX_BUNDLE_BYTES = 8 * 1024 * 1024
MAX_EVIDENCE_BYTES = 512 * 1024
_METHODS = frozenset(('get', 'put', 'post', 'delete', 'options', 'head', 'patch', 'trace'))
_POLICY_PATHS = frozenset(('privacy.html', 'terms.html', 'refund.html', 'contact.html', 'footer.html'))
_SEGMENT = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]*\Z')
_RESERVED = re.compile(r'(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\.|\Z)', re.I)
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')


def _fail():
    raise ValueError('invalid_pipeline_artifact')


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n'


def _path(path: str) -> str:
    if type(path) is not str or not 1 <= len(path) <= 240 or '\\' in path:
        _fail()
    parts = path.split('/')
    if len(parts) > 12 or any(not _SEGMENT.fullmatch(part) or part.endswith('.')
                            or _RESERVED.match(part) for part in parts):
        _fail()
    return path


def _files(files, stage: str | None = None, *, max_files=MAX_FILES, max_bytes=MAX_STAGE_BYTES) -> dict[str, str]:
    if type(files) is not list or not 1 <= len(files) <= max_files:
        _fail()
    result, seen, total = {}, set(), 0
    for file in files:
        if type(file) is not dict or file.keys() != {'path', 'content'}:
            _fail()
        path, content = _path(file['path']), file['content']
        if stage is not None and not path.startswith(stage + '/'):
            _fail()
        folded = path.casefold()
        if folded in seen or type(content) is not str or '\x00' in content:
            _fail()
        try:
            size = len(content.encode('utf-8'))
        except UnicodeError:
            _fail()
        total += size
        if size > MAX_FILE_BYTES or total > max_bytes:
            _fail()
        seen.add(folded)
        result[path] = content
    if any('/'.join(path.split('/')[:index]) in seen
           for path in seen for index in range(1, len(path.split('/')))):
        _fail()
    return result


def _json(content):
    def unique(pairs):
        value = {}
        for key, child in pairs:
            if key in value:
                _fail()
            value[key] = child
        return value

    try:
        value = json.loads(content, object_pairs_hook=unique,
                           parse_constant=lambda _: _fail())
    except (ValueError, TypeError, RecursionError, OverflowError):
        _fail()
    # A small, iterative traversal bounds later validation and reference walking.
    pending, count = [(value, 0)], 0
    while pending:
        child, depth = pending.pop()
        count += 1
        if count > 20_000 or depth > 24:
            _fail()
        if isinstance(child, dict):
            pending.extend((item, depth + 1) for item in child.values())
        elif isinstance(child, list):
            pending.extend((item, depth + 1) for item in child)
    return value


def _dependency_files(dependencies, stage):
    if type(dependencies) is not dict:
        _fail()
    artifact = dependencies.get(stage)
    if artifact is None:
        return {}
    if type(artifact) is not dict:
        _fail()
    return _files(artifact.get('files'), stage)


def _schema_from(dependencies):
    files = _dependency_files(dependencies, 'erd')
    if 'erd/schema.json' not in files:
        return None
    schema = _json(files['erd/schema.json'])
    if not validate_schema(schema)['valid'] or schema['database'] != 'sqlite':
        _fail()
    return schema


def build_database(schema) -> dict:
    """Generate and execute schema SQL in a fresh, nonpersistent SQLite database."""
    if not validate_schema(schema)['valid']:
        raise ValueError('invalid_schema')
    if schema['database'] != 'sqlite':
        raise ValueError('pipeline_sqlite_required')
    schema = deepcopy(schema)
    sql = export_sql(schema)
    try:
        with sqlite3.connect(':memory:') as connection:
            connection.execute('PRAGMA foreign_keys=ON')
            steps = 0

            def bounded():
                nonlocal steps
                steps += 1
                return steps > 10_000

            connection.set_progress_handler(bounded, 1000)
            connection.executescript(sql)
            enabled = connection.execute('PRAGMA foreign_keys').fetchone()[0] == 1
            violations = connection.execute('PRAGMA foreign_key_check').fetchall()
            integrity = connection.execute('PRAGMA integrity_check').fetchall()
            tables = sorted(row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT GLOB 'sqlite_*'"))
            if not enabled or violations or integrity != [('ok',)] or tables != sorted(
                    entity['name'] for entity in schema['entities']):
                raise ValueError('pipeline_database_check_failed')
    except sqlite3.Error:
        raise ValueError('pipeline_database_check_failed') from None
    finally:
        if 'connection' in locals():
            connection.close()
    checks = {'database': 'sqlite', 'schema_valid': True, 'sqlite_executed': True,
              'execution_scope': 'fresh_in_memory_empty_database',
              'foreign_keys_enabled': True, 'foreign_key_violations': 0,
              'integrity_check': 'ok', 'tables': tables,
              'schema_digest': hashlib.sha256(_dump(schema).encode('utf-8')).hexdigest(),
              'application_executed': False, 'persistent_database_created': False}
    return {'files': [{'path': 'database/schema.sql', 'content': sql},
                      {'path': 'database/checks.json', 'content': _dump(checks)}],
            'notes': ['SQLite 스키마를 새 메모리 데이터베이스에서 확인했습니다. 실제 데이터와 애플리케이션 동작은 검사하지 않았습니다.'],
            'checks': checks}


class _HTML(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=True)
        self.starts, self.ends, self.links, self.footer_comments = [], [], [], 0
        self.script_sources, self.stylesheets = [], []
        try:
            self.feed(content)
            self.close()
        except Exception:
            _fail()
        if self.starts.count('html') != 1 or self.starts.count('body') != 1 \
                or self.ends.count('html') != 1 or self.ends.count('body') != 1:
            _fail()
        if 'base' in self.starts:
            _fail()

    def handle_starttag(self, tag, attrs):
        self.starts.append(tag)
        if len({key for key, _ in attrs}) != len(attrs):
            _fail()
        attributes = dict(attrs)
        if tag == 'script' and attributes.get('src'):
            self.script_sources.append(attributes['src'])
        if tag == 'link' and 'stylesheet' in (attributes.get('rel') or '').lower().split() \
                and attributes.get('href'):
            self.stylesheets.append(attributes['href'])
        for key, value in attrs:
            if key in ('src', 'href', 'action') and value:
                self.links.append(value)

    def handle_endtag(self, tag):
        self.ends.append(tag)

    def handle_comment(self, data):
        if data == ' CHANNELSHIFT_SITE_FOOTER ':
            self.footer_comments += 1


def _screen_checks(files, dependencies):
    if not {'wireframe/index.html', 'wireframe/screens.json'} <= files.keys():
        _fail()
    data = _json(files['wireframe/screens.json'])
    if type(data) is not dict or data.keys() != {'screens'} \
            or type(data['screens']) is not list or not 1 <= len(data['screens']) <= 24:
        _fail()
    known_requirements = dependencies.get('confirmed_requirement_ids')
    if known_requirements is not None and (type(known_requirements) is not list
            or not 1 <= len(known_requirements) <= 64
            or any(type(item) is not str or not re.fullmatch(r'REQ-[0-9]{3}', item)
                   for item in known_requirements)
            or len(set(known_requirements)) != len(known_requirements)):
        _fail()
    ids, paths, covered = set(), set(), set()
    for screen in data['screens']:
        if type(screen) is not dict or screen.keys() != {'id', 'title', 'path', 'requirement_ids'}:
            _fail()
        key, title, path, requirements = (screen[x] for x in ('id', 'title', 'path', 'requirement_ids'))
        if type(key) is not str or re.fullmatch(r'SCREEN-[0-9]{3}', key) is None or key in ids:
            _fail()
        if type(title) is not str or not title.strip() or len(title) > 200:
            _fail()
        if type(path) is not str or len(path) > 200 or not path.startswith('/') \
                or path.startswith('//') or urlsplit(path).query or urlsplit(path).fragment \
                or '\\' in path or '..' in path.split('/') or path in paths \
                or any(char.isspace() or ord(char) < 32 for char in path):
            _fail()
        if type(requirements) is not list or not 1 <= len(requirements) <= 64 \
                or any(type(item) is not str or not re.fullmatch(r'REQ-[0-9]{3}', item)
                       for item in requirements) or len(set(requirements)) != len(requirements):
            _fail()
        if known_requirements is not None and not set(requirements) <= set(known_requirements):
            _fail()
        covered.update(requirements)
        ids.add(key)
        paths.add(path)
    if known_requirements is not None and covered != set(known_requirements):
        _fail()
    html_count = sum(1 for path, content in files.items()
                     if path.lower().endswith('.html') and _HTML(content))
    return {'screens_count': len(ids), 'html_pages_parsed': html_count,
            'requirement_id_syntax_checked': True,
            'requirement_id_membership_checked': known_requirements is not None,
            'requirement_declarations_checked': known_requirements is not None,
            'requirement_declarations_covered': sorted(covered),
            'requirement_coverage_verified': False}


def _screens_from(dependencies):
    files = _dependency_files(dependencies, 'wireframe')
    if not files:
        return None
    _screen_checks(files, dependencies)
    return _json(files['wireframe/screens.json'])['screens']


def _resolve_ref(document, ref):
    if type(ref) is not str or not ref.startswith('#/') or len(ref) > 500:
        _fail()
    node = document
    for part in ref[2:].split('/'):
        if re.search(r'~(?![01])', part):
            _fail()
        key = part.replace('~1', '/').replace('~0', '~')
        if isinstance(node, dict) and key in node:
            node = node[key]
        elif isinstance(node, list) and re.fullmatch(r'0|[1-9][0-9]*', key) \
                and int(key) < len(node):
            node = node[int(key)]
        else:
            _fail()
    if not isinstance(node, dict):
        _fail()
    return node


def _openapi(files, dependencies):
    if 'api/openapi.json' not in files:
        _fail()
    data = _json(files['api/openapi.json'])
    if type(data) is not dict or type(data.get('openapi')) is not str \
            or not re.fullmatch(r'3\.[01]\.[0-9]+', data['openapi']) \
            or type(data.get('info')) is not dict:
        _fail()
    if any(type(data['info'].get(key)) is not str or not data['info'][key].strip()
           for key in ('title', 'version')):
        _fail()
    paths = data.get('paths')
    if type(paths) is not dict or not 1 <= len(paths) <= 100:
        _fail()
    pending, refs = [data], 0
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            if '$ref' in node:
                _resolve_ref(data, node['$ref'])
                refs += 1
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    schema = _schema_from(dependencies)
    tables = {entity['name'] for entity in schema['entities']} if schema is not None else None
    operation_ids, operations, database_links = set(), [], 0
    for path, entry in paths.items():
        if not path.startswith('/') or path.startswith('//') or len(path) > 200 \
                or any(c in path for c in ('?', '#', '\\')) or '..' in path.split('/') \
                or any(char.isspace() or ord(char) < 32 for char in path) or type(entry) is not dict:
            _fail()
        template_names = re.findall(r'\{([^{}]+)\}', path)
        if len(template_names) != path.count('{') or len(template_names) != path.count('}') \
                or any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name) for name in template_names):
            _fail()
        path_operations = [key for key in entry if key in _METHODS]
        if not path_operations:
            _fail()
        if any(key not in _METHODS | {'summary', 'description', 'servers', 'parameters'}
               and not key.startswith('x-') for key in entry):
            _fail()
        for method in path_operations:
            operation = entry[method]
            if type(operation) is not dict:
                _fail()
            operation_id, responses = operation.get('operationId'), operation.get('responses')
            if type(operation_id) is not str or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,99}', operation_id) \
                    or operation_id in operation_ids or type(responses) is not dict or not responses:
                _fail()
            for code, response in responses.items():
                if not re.fullmatch(r'(?:[1-5][0-9X]{2}|default)', code) or type(response) is not dict:
                    _fail()
                resolved = _resolve_ref(data, response['$ref']) if '$ref' in response else response
                if type(resolved.get('description')) is not str or not resolved['description'].strip():
                    _fail()
            parameters = entry.get('parameters', [])
            own_parameters = operation.get('parameters', [])
            if type(parameters) is not list or type(own_parameters) is not list:
                _fail()
            path_parameters = set()
            for parameter in parameters + own_parameters:
                if type(parameter) is not dict:
                    _fail()
                parameter = _resolve_ref(data, parameter['$ref']) if '$ref' in parameter else parameter
                if parameter.get('in') not in ('path', 'query', 'header', 'cookie') \
                        or type(parameter.get('name')) is not str or not parameter['name']:
                    _fail()
                if parameter['in'] == 'path':
                    if parameter.get('required') is not True:
                        _fail()
                    path_parameters.add(parameter['name'])
            if path_parameters != set(template_names):
                _fail()
            if 'x-channelshift-table' in operation:
                table = operation['x-channelshift-table']
                if type(table) is not str or tables is None or table not in tables:
                    _fail()
                database_links += 1
            operation_ids.add(operation_id)
            operations.append(method.upper() + ' ' + path)
    screens = _screens_from(dependencies)
    links = contracts.api_links(data, schema, screens,
                                dependencies.get('confirmed_requirement_ids'))
    schemas_checked = contracts.validate_api_schemas(data, _resolve_ref)
    return data, {'openapi_version': data['openapi'], 'operations': sorted(operations),
                  'local_references_checked': refs, 'database_links_checked': database_links,
                  'schema_definitions_checked': schemas_checked,
                  'schema_validation_profile': 'channelshift-json-schema-subset/v1',
                  'operation_link_declarations_checked': True, 'operation_links': links,
                  'operation_requirement_membership_checked': screens is not None
                      or dependencies.get('confirmed_requirement_ids') is not None,
                  'operation_field_membership_checked': schema is not None,
                  'operation_screen_membership_checked': screens is not None,
                  'full_openapi_validation_performed': False}


def _api_paths(dependencies):
    files = _dependency_files(dependencies, 'api')
    if not files:
        return set()
    document, _ = _openapi(files, dependencies)
    return set(document['paths'])


def _api_match(path, api_paths):
    for candidate in api_paths:
        pattern = ''.join('[^/]+' if part.startswith('{') and part.endswith('}')
                          else re.escape(part) for part in re.split(r'(\{[^{}]+\})', candidate))
        if re.fullmatch(pattern, path):
            return True
    return False


def _local_link(target, source, files, api_paths):
    try:
        parsed = urlsplit(target)
    except ValueError:
        _fail()
    if parsed.scheme or parsed.netloc:
        if parsed.scheme not in ('http', 'https', 'mailto', 'tel') and parsed.scheme:
            _fail()
        return 'external'
    if not parsed.path:
        return 'fragment'
    path = unquote(parsed.path)
    if '\\' in path or '\x00' in path or '%' in path or '..' in path.split('/'):
        _fail()
    if path.startswith('/api/') or _api_match(path, api_paths):
        if not _api_match(path, api_paths):
            _fail()
        return 'api'
    if path.startswith('/'):
        resolved = 'frontend/' + path.lstrip('/')
    else:
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source), path))
    if resolved == 'frontend/' or path == '/':
        resolved = 'frontend/index.html'
    if resolved.endswith('/'):
        resolved += 'index.html'
    if not resolved.startswith('frontend/'):
        _fail()
    if resolved not in files and resolved not in {'frontend/' + name for name in _POLICY_PATHS}:
        _fail()
    return 'local'


def _frontend_checks(files, dependencies):
    required = {'frontend/index.html', 'frontend/app.js', 'frontend/style.css', 'frontend/screens.json'}
    if not required <= files.keys() or any(path.casefold() in {'frontend/' + name for name in _POLICY_PATHS}
                                        for path in files):
        _fail()
    index = files['frontend/index.html']
    index_document = _HTML(index)
    if index.count(FOOTER_MARKER) != 1 or index_document.footer_comments != 1 \
            or not {'/' + path for path in _POLICY_PATHS - {'footer.html'}} <= set(index_document.links):
        _fail()

    def matches_resource(target, filename):
        try:
            parsed = urlsplit(target)
        except ValueError:
            _fail()
        return not parsed.scheme and not parsed.netloc and unquote(parsed.path) in (
            '/' + filename, filename, './' + filename)

    if not any(matches_resource(target, 'app.js') for target in index_document.script_sources) \
            or not any(matches_resource(target, 'style.css') for target in index_document.stylesheets):
        _fail()
    api_files = _dependency_files(dependencies, 'api')
    if not api_files:
        _fail()
    api, api_checks = _openapi(api_files, dependencies)
    api_paths = set(api['paths'])
    operations = api_checks['operation_links']
    counts = {'html_pages_parsed': 0, 'local_links_checked': 0, 'api_literal_paths_checked': 0,
              'external_links_not_checked': 0, 'fetch_calls_detected': 0,
              'dynamic_fetch_calls_not_checked': 0, 'javascript_syntax_validated': False,
              'api_literal_methods_checked': 0}
    counts.update(contracts.frontend_bindings(files, operations, _screens_from(dependencies), _json))
    for path, content in files.items():
        if path.lower().endswith('.html'):
            document = _HTML(content)
            counts['html_pages_parsed'] += 1
            if content.count(FOOTER_MARKER) > 1 or content.count(FOOTER_MARKER) != document.footer_comments:
                _fail()
            body_open = re.search(r'<body(?:\s[^>]*)?>', content, re.I)
            body_close = list(re.finditer(r'</body\s*>', content, re.I))
            if not body_open or len(body_close) != 1 or body_open.end() > body_close[0].start():
                _fail()
            if FOOTER_MARKER in content and not body_open.end() <= content.index(FOOTER_MARKER) < body_close[0].start():
                _fail()
            for target in document.links:
                kind = _local_link(target, path, files, api_paths)
                if kind == 'local':
                    counts['local_links_checked'] += 1
                elif kind == 'api':
                    counts['api_literal_paths_checked'] += 1
                elif kind == 'external':
                    counts['external_links_not_checked'] += 1
        if path.lower().endswith(('.js', '.html')):
            sources = [content] if path.lower().endswith('.js') else contracts.inline_scripts(content)
            for target, method in (call for source in sources for call in contracts.literal_fetches(source)):
                counts['fetch_calls_detected'] += 1
                if target is None:
                    counts['dynamic_fetch_calls_not_checked'] += 1
                    continue
                kind = _local_link(target, path, files, api_paths)
                if kind == 'api':
                    counts['api_literal_paths_checked'] += 1
                    if method is not None:
                        actual_path = unquote(urlsplit(target).path)
                        if not any(operation['method'] == method and _api_match(actual_path, {operation['path']})
                                   for operation in operations):
                            _fail()
                        counts['api_literal_methods_checked'] += 1
                    else:
                        counts['dynamic_fetch_calls_not_checked'] += 1
                elif kind == 'external':
                    counts['external_links_not_checked'] += 1
    return counts


def validate_stage(stage, files, dependencies) -> dict:
    """Check syntax/declared links only; generated Python/JS never executes."""
    if stage not in STAGES or type(dependencies) is not dict:
        _fail()
    entries = _files(files, stage)
    checks = {'stage': stage, 'files_checked': len(entries), 'application_execution_performed': False,
              'runtime_behavior_verified': False}
    if stage == 'wireframe':
        checks.update(_screen_checks(entries, dependencies))
    elif stage == 'erd':
        schema = _schema_from({'erd': {'files': files}})
        if schema is None:
            _fail()
        checks.update({'schema_valid': True, 'database': 'sqlite'})
    elif stage == 'database':
        schema = _schema_from(dependencies)
        if schema is None:
            _fail()
        generated = build_database(schema)
        if entries != _files(generated['files'], 'database'):
            _fail()
        checks.update(generated['checks'])
    elif stage == 'api':
        _, result = _openapi(entries, dependencies)
        checks.update(result)
    elif stage == 'backend':
        api_files = _dependency_files(dependencies, 'api')
        if not api_files:
            _fail()
        _, api_checks = _openapi(api_files, dependencies)
        checks.update(contracts.backend_bindings(entries, api_checks['operation_links'], _json))
    elif stage == 'frontend':
        checks.update(_frontend_checks(entries, dependencies))
    return checks


def _files_digest(files):
    canonical = [{'path': path, 'content': content} for path, content in sorted(files.items())]
    return hashlib.sha256(_dump(canonical).encode('utf-8')).hexdigest()


def _footer_into(content, footer):
    if FOOTER_MARKER in content:
        if content.count(FOOTER_MARKER) != 1:
            _fail()
        return content.replace(FOOTER_MARKER, footer)
    matches = list(re.finditer(r'</body\s*>', content, re.I))
    if len(matches) != 1:
        _fail()
    at = matches[0].start()
    return content[:at] + footer + content[at:]


def _evidence_files(evidence):
    if evidence is None:
        return {}
    if type(evidence) is not dict or evidence.keys() != {'requirements', 'reviews', 'delivery'} \
            or any(type(value) is not dict for value in evidence.values()):
        _fail()
    pending, nodes = [(evidence, 0)], 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if depth > 24 or nodes > 20_000:
            _fail()
        if type(value) is dict:
            if any(type(key) is not str or len(key) > 200 for key in value):
                _fail()
            pending.extend((child, depth + 1) for child in value.values())
        elif type(value) is list:
            pending.extend((child, depth + 1) for child in value)
        elif type(value) is str:
            if len(value) > MAX_EVIDENCE_BYTES:
                _fail()
        elif value is not None and type(value) not in (bool, int, float):
            _fail()
    try:
        files = {'evidence/' + key + '.json': _dump(value) for key, value in evidence.items()}
        if sum(len(content.encode('utf-8')) for content in files.values()) > MAX_EVIDENCE_BYTES:
            _fail()
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        _fail()
    return files


def bundle(name, artifacts, obligations, *, evidence=None) -> bytes:
    """Package caller-selected artifacts; caller must enforce its approval gates."""
    if type(name) is not str or not name.strip() or len(name) > 200 \
            or any(ord(char) < 32 for char in name) \
            or type(artifacts) is not dict or artifacts.keys() != set(STAGES):
        _fail()
    policy_files = render_pages(obligations, name)
    evidence_files = _evidence_files(evidence)
    files, metadata, total = {}, {}, 0
    for stage in STAGES:
        artifact = artifacts[stage]
        if type(artifact) is not dict:
            _fail()
        entries = _files(artifact.get('files'), stage)
        checks = validate_stage(stage, artifact['files'], artifacts)
        digest = artifact.get('digest')
        if digest is not None and (type(digest) is not str or not _DIGEST.fullmatch(digest)):
            _fail()
        metadata[stage] = {'source_digest': digest, 'files_digest': _files_digest(entries), 'checks': checks}
        files.update(entries)
    footer = next(file['content'] for file in policy_files if file['path'] == 'footer.html')
    for path in list(files):
        if path.startswith('frontend/') and path.lower().endswith('.html'):
            files[path] = _footer_into(files[path], footer)
    for file in policy_files:
        path = 'frontend/' + file['path']
        if path.casefold() in {key.casefold() for key in files}:
            _fail()
        files[path] = file['content']
    files.update(evidence_files)
    files['README.md'] = (
        '# Generated application bundle\n\n'
        'Extract the archive into a new directory. Read backend/README.md and review the files before starting.\n\n'
        'From the extracted bundle root, start the Python standard-library application manually:\n\n'
        '```sh\npython backend/app.py\n```\n\n'
        'Use the local URL documented by the backend. The backend is expected to serve frontend/ at the site root '
        'and initialize its application SQLite database from database/schema.sql on first launch. '
        'Application launch and initialization have not been performed by the export pipeline.\n\n'
        'The schema SQL was executed only in a new, empty in-memory SQLite database with foreign keys enabled. '
        'Backend Python route declarations, handler symbols and test definitions were inspected without '
        'importing or running them. Declared API requirements, database fields and screen mappings, '
        'bounded request/response schemas, frontend HTML and supported literal fetch methods/paths '
        'were checked without running JavaScript. Dynamic calls remain unchecked. Test definitions '
        'are not test results. These checks do not establish runtime behavior, '
        'API implementation correctness, security, or legal sufficiency.\n\n'
        'Required site information and policies are included in frontend/. Check their factual accuracy '
        'and applicability before publication. No deployment or production database change was performed.\n'
    )
    manifest = {'format': 'channelshift.pipeline-bundle/v1', 'name': name.strip(),
                'artifacts': metadata, 'application_execution_performed': False,
                'deployment_performed': False, 'persistent_database_created': False,
                'database_validation_scope': 'fresh_in_memory_empty_sqlite_database',
                'legal_review_required': True, 'approval_verification_owner': 'calling_workspace',
                'export_files': [{'path': path, 'sha256': hashlib.sha256(content.encode('utf-8')).hexdigest()}
                                 for path, content in sorted(files.items())]}
    files['manifest.json'] = _dump(manifest)
    # Validate the final namespace too, including generated files and case collisions.
    _files([{'path': path, 'content': content} for path, content in files.items()],
           max_files=MAX_FILES * len(STAGES) + 10, max_bytes=MAX_BUNDLE_BYTES)
    for content in files.values():
        total += len(content.encode('utf-8'))
    if total > MAX_BUNDLE_BYTES:
        _fail()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, content in sorted(files.items()):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content.encode('utf-8'), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return output.getvalue()
