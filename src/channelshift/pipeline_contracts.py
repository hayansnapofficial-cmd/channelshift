"""Bounded declaration checks. Source and test files are never executed.

These checks connect declared requirements, screens, API operations and source
symbols. They establish neither behavior nor test success nor security.
"""
from __future__ import annotations

import ast
from copy import deepcopy
from html.parser import HTMLParser
import re


METHODS = frozenset(('get', 'put', 'post', 'delete', 'options', 'head', 'patch', 'trace'))
_ID = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')
_JS_IDENTIFIER = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*')
_TYPES = frozenset(('object', 'array', 'string', 'integer', 'number', 'boolean', 'null'))
_SCHEMA_KEYS = frozenset(('$ref', 'type', 'properties', 'required', 'additionalProperties',
    'items', 'enum', 'const', 'title', 'description', 'default', 'example', 'examples',
    'format', 'nullable', 'readOnly', 'writeOnly', 'deprecated', 'minLength', 'maxLength',
    'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'minItems', 'maxItems',
    'uniqueItems', 'minProperties', 'maxProperties', 'pattern', 'multipleOf',
    'allOf', 'anyOf', 'oneOf', 'not'))


def _fail():
    raise ValueError('invalid_pipeline_artifact')


def _strings(value, *, pattern=None, minimum=0, maximum=100):
    if type(value) is not list or not minimum <= len(value) <= maximum \
            or any(type(item) is not str or not item or len(item) > 200 for item in value) \
            or len(set(value)) != len(value) \
            or (pattern and any(not re.fullmatch(pattern, item) for item in value)):
        _fail()
    return value


def api_links(document, schema, screens, known_requirements):
    """Return normalized links from operations after checking supplied contexts."""
    fields = ({entity['name'] + '.' + attribute['name'] for entity in schema['entities']
               for attribute in entity['attributes']} if schema is not None else None)
    screen_ids = {screen['id'] for screen in screens} if screens is not None else None
    requirements = set(_strings(known_requirements, pattern=r'REQ-[0-9]{3}', minimum=1, maximum=64)) \
        if known_requirements is not None else ({requirement for screen in screens
            for requirement in screen['requirement_ids']} if screens is not None else None)
    result = []
    for path, entry in document['paths'].items():
        for method, operation in entry.items():
            if method not in METHODS:
                continue
            reqs = _strings(operation.get('x-channelshift-requirement-ids'),
                            pattern=r'REQ-[0-9]{3}', minimum=1, maximum=64)
            columns = _strings(operation.get('x-channelshift-fields'),
                               pattern=r'[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*', maximum=100)
            views = _strings(operation.get('x-channelshift-screens'),
                             pattern=r'SCREEN-[0-9]{3}', maximum=24)
            if (requirements is not None and not set(reqs) <= requirements) \
                    or (fields is not None and not set(columns) <= fields) \
                    or (screen_ids is not None and not set(views) <= screen_ids):
                _fail()
            if 'x-channelshift-table' in operation and any(
                    not column.startswith(operation['x-channelshift-table'] + '.') for column in columns):
                _fail()
            result.append({'operation_id': operation['operationId'], 'method': method.upper(),
                           'path': path, 'requirement_ids': sorted(reqs), 'fields': sorted(columns),
                           'screen_ids': sorted(views)})
            if len(result) > 100:
                _fail()
    return sorted(result, key=lambda item: item['operation_id'])


def validate_api_schemas(document, resolve):
    """Validate an explicit bounded JSON Schema subset; never claim full OAS validation."""
    count = 0

    def check(value, depth=0, refs=()):
        nonlocal count
        count += 1
        if count > 10_000 or depth > 24 or type(value) is not dict or not value \
                or any(key not in _SCHEMA_KEYS and not key.startswith('x-') for key in value):
            _fail()
        if '$ref' in value:
            ref = value['$ref']
            if type(ref) is not str or ref in refs:
                _fail()
            check(resolve(document, ref), depth + 1, refs + (ref,))
        kind = value.get('type')
        kinds = kind if type(kind) is list else [kind] if kind is not None else []
        if kinds and (any(type(item) is not str or item not in _TYPES for item in kinds)
                      or len(set(kinds)) != len(kinds)):
            _fail()
        if 'type' in value and not kinds:
            _fail()
        if not kinds and not any(key in value for key in ('$ref', 'allOf', 'anyOf', 'oneOf', 'not', 'enum', 'const')):
            _fail()
        for key in ('allOf', 'anyOf', 'oneOf'):
            if key in value:
                if type(value[key]) is not list or not 1 <= len(value[key]) <= 16:
                    _fail()
                for child in value[key]:
                    check(child, depth + 1, refs)
        if 'not' in value:
            check(value['not'], depth + 1, refs)
        if 'properties' in value:
            props = value['properties']
            if (kinds and 'object' not in kinds) or type(props) is not dict or len(props) > 100 \
                    or any(not key or len(key) > 200 for key in props):
                _fail()
            for child in props.values():
                check(child, depth + 1, refs)
        if 'required' in value:
            required = _strings(value['required'])
            if not set(required) <= set(value.get('properties', {})):
                _fail()
        if 'additionalProperties' in value:
            extra = value['additionalProperties']
            if type(extra) is not bool:
                check(extra, depth + 1, refs)
        if 'array' in kinds and 'items' not in value:
            _fail()
        if 'items' in value:
            if kinds and 'array' not in kinds:
                _fail()
            check(value['items'], depth + 1, refs)
        for key in ('title', 'description', 'format', 'pattern'):
            if key in value and type(value[key]) is not str:
                _fail()
        for key in ('nullable', 'readOnly', 'writeOnly', 'deprecated', 'uniqueItems'):
            if key in value and type(value[key]) is not bool:
                _fail()
        for low, high in (('minLength', 'maxLength'), ('minItems', 'maxItems'), ('minProperties', 'maxProperties')):
            for key in (low, high):
                if key in value and (type(value[key]) is not int or value[key] < 0):
                    _fail()
            if low in value and high in value and value[low] > value[high]:
                _fail()
        for key in ('minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf'):
            if key in value and type(value[key]) not in (int, float):
                _fail()
        if ('minimum' in value and 'maximum' in value and value['minimum'] > value['maximum']) \
                or ('multipleOf' in value and value['multipleOf'] <= 0):
            _fail()
        if 'examples' in value and type(value['examples']) is not list:
            _fail()
        if 'enum' in value:
            items = value['enum']
            if type(items) is not list or not 1 <= len(items) <= 100:
                _fail()
            for index, item in enumerate(items):
                if any(type(item) is type(previous) and item == previous for previous in items[:index]):
                    _fail()
                if kinds and not _matches_type(item, kinds, nullable=value.get('nullable', False)):
                    _fail()
        if 'const' in value and kinds and not _matches_type(value['const'], kinds, nullable=value.get('nullable', False)):
            _fail()

    def resolved(value):
        seen = set()
        while type(value) is dict and '$ref' in value:
            ref = value['$ref']
            if type(ref) is not str or ref in seen or len(seen) > 24:
                _fail()
            seen.add(ref)
            value = resolve(document, ref)
        if type(value) is not dict:
            _fail()
        return value

    def content(value):
        if type(value) is not dict or not value or len(value) > 8:
            _fail()
        for media, definition in value.items():
            if type(definition) is not dict or not re.fullmatch(r'application/(?:[A-Za-z0-9.+-]+\+)?json', media) \
                    or 'schema' not in definition:
                _fail()
            check(definition['schema'])

    components = document.get('components', {})
    if type(components) is not dict:
        _fail()
    schemas = components.get('schemas', {})
    if type(schemas) is not dict or len(schemas) > 100:
        _fail()
    for definition in schemas.values():
        check(definition)
    for entry in document['paths'].values():
        for method, operation in entry.items():
            if method not in METHODS:
                continue
            if 'requestBody' in operation:
                body = resolved(operation['requestBody'])
                if ('required' in body and type(body['required']) is not bool) or 'content' not in body:
                    _fail()
                content(body['content'])
            for response in operation['responses'].values():
                response = resolved(response)
                if 'content' in response:
                    content(response['content'])
            for parameter in entry.get('parameters', []) + operation.get('parameters', []):
                parameter = resolved(parameter)
                if 'required' in parameter and type(parameter['required']) is not bool:
                    _fail()
                if ('schema' in parameter) == ('content' in parameter):
                    _fail()
                if 'schema' in parameter:
                    check(parameter['schema'])
                else:
                    content(parameter['content'])
    return count


def _matches_type(value, kinds, *, nullable=False):
    actual = ('null' if value is None else 'boolean' if type(value) is bool else
              'integer' if type(value) is int else 'number' if type(value) is float else
              'string' if type(value) is str else 'array' if type(value) is list else 'object')
    return actual in kinds or (actual == 'integer' and 'number' in kinds) or (actual == 'null' and nullable)


def parse_python(files):
    trees = {}
    for path, source in files.items():
        if path.lower().endswith('.py'):
            try:
                tree = ast.parse(source, filename=path, mode='exec', feature_version=(3, 10))
            except (SyntaxError, ValueError, RecursionError, MemoryError):
                _fail()
            if sum(1 for _ in ast.walk(tree)) > 50_000:
                _fail()
            trees[path] = tree
    return trees


def _functions(nodes):
    result = {}
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in result:
                _fail()
            result[node.name] = node
    return result


def _own_nodes(function):
    """Exclude nested definitions: their assertions do not belong to this test."""
    pending = list(function.body)
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            pending.extend(ast.iter_child_nodes(node))


def _nonplaceholder(function):
    statements = [node for node in function.body if not (
        isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str))]
    if not statements:
        return False
    for node in statements:
        if isinstance(node, ast.Pass) or (isinstance(node, ast.Expr) and
                isinstance(node.value, ast.Constant) and node.value.value is Ellipsis):
            continue
        if isinstance(node, ast.Return) and (node.value is None or
                isinstance(node.value, ast.Constant) and node.value.value in (None, Ellipsis)):
            continue
        if isinstance(node, ast.Raise):
            exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            if isinstance(exc, ast.Name) and exc.id == 'NotImplementedError':
                continue
        return True
    return False


def backend_bindings(files, operations, parse_json):
    if not {'backend/app.py', 'backend/README.md', 'backend/routes.json'} <= files.keys():
        _fail()
    trees = parse_python(files)
    functions = _functions(trees['backend/app.py'].body)
    route_tables = [node.value for node in trees['backend/app.py'].body if
        (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'ROUTES'
                                             for target in node.targets)) or
        (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == 'ROUTES')]
    if len(route_tables) != 1 or not isinstance(route_tables[0], ast.Dict):
        _fail()
    routes = {}
    for key, value in zip(route_tables[0].keys, route_tables[0].values):
        if not isinstance(key, ast.Tuple) or len(key.elts) != 2 \
                or any(not isinstance(part, ast.Constant) or type(part.value) is not str for part in key.elts) \
                or not isinstance(value, ast.Name):
            _fail()
        method, path = (part.value for part in key.elts)
        if (method, path) in routes:
            _fail()
        routes[method, path] = value.id
    manifest = parse_json(files['backend/routes.json'])
    if type(manifest) is not dict or manifest.keys() != {'routes'} \
            or type(manifest['routes']) is not list or not 1 <= len(manifest['routes']) <= 100:
        _fail()
    by_id = {operation['operation_id']: operation for operation in operations}
    expected = {(operation['method'], operation['path']) for operation in operations}
    if set(routes) != expected:
        _fail()
    bindings, seen = [], set()
    for binding in manifest['routes']:
        if type(binding) is not dict or binding.keys() != {'operation_id', 'handler', 'test_file', 'test_symbol'} \
                or any(type(value) is not str for value in binding.values()):
            _fail()
        key, handler, test_file, symbol = (binding[name] for name in
                                         ('operation_id', 'handler', 'test_file', 'test_symbol'))
        if key not in by_id or key in seen or handler not in functions or not _ID.fullmatch(handler) \
                or not _nonplaceholder(functions[handler]) \
                or not re.fullmatch(r'backend/(?:[A-Za-z0-9_.-]+/)*test[A-Za-z0-9_.-]*\.py', test_file) \
                or test_file not in trees:
            _fail()
        operation = by_id[key]
        if routes[operation['method'], operation['path']] != handler:
            _fail()
        parts = symbol.split('.')
        if len(parts) not in (1, 2) or any(not _ID.fullmatch(part) for part in parts) or not parts[-1].startswith('test_'):
            _fail()
        nodes = trees[test_file].body
        if len(parts) == 2:
            classes = [node for node in nodes if isinstance(node, ast.ClassDef) and node.name == parts[0]]
            if len(classes) != 1:
                _fail()
            nodes = classes[0].body
        test = _functions(nodes).get(parts[-1])
        if test is None or not any(isinstance(node, ast.Assert) or (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == 'self'
            and node.func.attr.startswith('assert')) for node in _own_nodes(test)):
            _fail()
        seen.add(key)
        bindings.append(dict(binding, method=operation['method'], path=operation['path']))
    if seen != set(by_id):
        _fail()
    return {'python_files_parsed': len(trees), 'python_syntax_target': '3.10',
            'imports_executed': False, 'dependency_availability_verified': False,
            'route_declarations_checked': True, 'test_definitions_checked': True,
            'test_definitions_count': len(bindings),
            'test_execution_performed': False,
            'route_bindings': sorted(bindings, key=lambda value: value['operation_id'])}


def frontend_bindings(files, operations, screens, parse_json):
    if 'frontend/screens.json' not in files or screens is None:
        _fail()
    value = parse_json(files['frontend/screens.json'])
    if type(value) is not dict or value.keys() != {'screens'} \
            or type(value['screens']) is not list or not 1 <= len(value['screens']) <= 24:
        _fail()
    by_id = {operation['operation_id']: operation for operation in operations}
    known = {screen['id'] for screen in screens}
    seen, bindings = set(), []
    for screen in value['screens']:
        if type(screen) is not dict or screen.keys() != {'screen_id', 'file', 'operation_ids'}:
            _fail()
        key, path = screen['screen_id'], screen['file']
        if type(key) is not str or key not in known or key in seen or type(path) is not str \
                or path not in files or not path.startswith('frontend/') or not path.endswith('.html'):
            _fail()
        operations_used = _strings(screen['operation_ids'])
        if not set(operations_used) <= by_id.keys():
            _fail()
        if any(key not in by_id[operation]['screen_ids'] for operation in operations_used):
            _fail()
        expected = {operation['operation_id'] for operation in operations if key in operation['screen_ids']}
        if set(operations_used) != expected:
            _fail()
        seen.add(key)
        bindings.append(deepcopy(screen))
    if seen != known:
        _fail()
    return {'screen_declarations_checked': True,
            'screen_bindings': sorted(bindings, key=lambda value: value['screen_id'])}


def inline_scripts(content):
    """Inspect executable inline script text, not HTML comments or attributes."""
    class Scripts(HTMLParser):
        def __init__(self):
            super().__init__()
            self.sources, self.parts = [], None

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if tag == 'script' and not attributes.get('src') and attributes.get('type', '').lower() in (
                    '', 'module', 'text/javascript', 'application/javascript'):
                self.parts = []

        def handle_data(self, data):
            if self.parts is not None:
                self.parts.append(data)

        def handle_endtag(self, tag):
            if tag == 'script' and self.parts is not None:
                self.sources.append(''.join(self.parts))
                self.parts = None

    parser = Scripts()
    parser.feed(content)
    parser.close()
    return parser.sources


def _js_tokens(source):
    """Small lexical scanner for literal fetch calls, deliberately not a JS parser.

    Escaped/interpolated literals are unknown. Comments, strings, and recognized
    regex literals cannot contribute identifiers or method keys.
    """
    tokens, index, length = [], 0, len(source)
    while index < length:
        char = source[index]
        if char.isspace():
            index += 1
            continue
        if source.startswith('//', index):
            end = source.find('\n', index + 2)
            index = length if end < 0 else end + 1
            continue
        if source.startswith('/*', index):
            end = source.find('*/', index + 2)
            index = length if end < 0 else end + 2
            continue
        if source.startswith('=>', index):
            tokens.append(('punct', '=>'))
            index += 2
            continue
        if char in ('"', "'", '`'):
            quote, start, literal = char, index + 1, True
            index += 1
            while index < length and source[index] != quote:
                if source[index] == '\\':
                    literal = False
                    index += 2
                else:
                    if quote == '`' and source.startswith('${', index):
                        literal = False
                    index += 1
            value = source[start:index]
            tokens.append(('string' if literal and index < length else 'unknown', value))
            index += 1
            continue
        # At expression starts slash opens a regex literal. Division is retained.
        previous = tokens[-1][1] if tokens else None
        if char == '/' and (previous is None or previous in (
                '=', '(', '[', '{', ',', ':', ';', '!', '?', '&', '|', 'return', '=>')):
            index += 1
            in_class = False
            while index < length:
                if source[index] == '\\':
                    index += 2
                    continue
                if source[index] == '[':
                    in_class = True
                elif source[index] == ']':
                    in_class = False
                elif source[index] == '/' and not in_class:
                    index += 1
                    break
                index += 1
            while index < length and source[index].isalpha():
                index += 1
            tokens.append(('unknown', 'regex'))
            continue
        identifier = _JS_IDENTIFIER.match(source, index)
        if identifier:
            value = identifier.group()
            tokens.append(('id', value))
            index += len(value)
        else:
            tokens.append(('punct', char))
            index += 1
        if len(tokens) > 100_000:
            _fail()
    return tokens


def literal_fetches(source):
    """Yield (literal URL or None, literal method or None); unknown stays unknown."""
    tokens = _js_tokens(source)
    for index, token in enumerate(tokens):
        if token != ('id', 'fetch') or index + 1 >= len(tokens) or tokens[index + 1][1] != '(':
            continue
        if index and tokens[index - 1][1] == '.' and (
                index < 2 or tokens[index - 2] not in (('id', 'window'), ('id', 'globalThis'))):
            continue
        cursor = index + 2
        if cursor + 1 >= len(tokens) or tokens[cursor][0] != 'string' \
                or tokens[cursor + 1][1] not in (',', ')'):
            yield None, None
            continue
        target = tokens[cursor][1]
        cursor += 1
        if tokens[cursor][1] == ')':
            yield target, 'GET'
            continue
        cursor += 1
        if cursor >= len(tokens) or tokens[cursor][1] != '{':
            yield target, None
            continue
        # Parse only a plain options object. Spread, computed keys, shorthand,
        # duplicate method keys and dynamic method values prevent method proof.
        depth, method, certain, methods = [], 'GET', True, 0
        cursor += 1
        expect_key = True
        while cursor < len(tokens):
            kind, value = tokens[cursor]
            if not depth and kind == 'punct' and value == '}':
                break
            if not depth and expect_key:
                if kind not in ('id', 'string') or cursor + 1 >= len(tokens) or tokens[cursor + 1][1] != ':':
                    certain = False
                elif value == '__proto__':
                    certain = False
                elif value == 'method':
                    methods += 1
                    if cursor + 3 >= len(tokens) or tokens[cursor + 2][0] != 'string' \
                            or tokens[cursor + 3][1] not in (',', '}'):
                        certain = False
                    else:
                        method = tokens[cursor + 2][1].upper()
                expect_key = False
            if kind == 'punct' and value in ('{', '[', '('):
                depth.append(value)
            elif kind == 'punct' and value in ('}', ']', ')') and depth:
                depth.pop()
            elif kind == 'punct' and value == ',' and not depth:
                expect_key = True
            cursor += 1
        if cursor + 1 >= len(tokens) or tokens[cursor + 1] != ('punct', ')') or methods > 1:
            certain = False
        yield target, method if certain else None
