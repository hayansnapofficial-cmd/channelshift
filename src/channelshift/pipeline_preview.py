"""Static, inert visual previews of supplied HTML/CSS; never a running app.

Serve returned bytes with a sandbox CSP, default-src 'none', style-src
'unsafe-inline', img-src data:, form-action 'none', base-uri 'none', and
frame-ancestors 'self'. The embedding iframe must have an empty sandbox.
No files, network resources, application code or CSS imports are loaded here.
"""
from __future__ import annotations

import base64
from html import escape
from html.parser import HTMLParser
import posixpath
import re
from urllib.parse import unquote, urlsplit


MAX_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024
_ALLOWED = frozenset(('div', 'span', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'header', 'footer', 'section', 'main', 'nav', 'article', 'aside', 'address',
    'blockquote', 'pre', 'code', 'kbd', 'samp', 'sub', 'sup', 'b', 'strong', 'em', 'i',
    'u', 's', 'small', 'mark', 'del', 'ins', 'br', 'hr', 'wbr', 'time', 'ul', 'ol',
    'li', 'dl', 'dt', 'dd', 'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td',
    'caption', 'colgroup', 'col', 'details', 'summary', 'a', 'figure', 'figcaption',
    'img', 'picture', 'label', 'fieldset', 'legend', 'input', 'textarea', 'select',
    'option', 'optgroup', 'button', 'progress', 'meter'))
_VOID = frozenset(('br', 'hr', 'wbr', 'img', 'input', 'col'))
_BLOCKED = frozenset(('script', 'iframe', 'object', 'embed', 'applet', 'svg', 'math',
                      'template', 'noscript', 'noembed', 'audio', 'video'))
_GLOBAL = frozenset(('id', 'class', 'title', 'lang', 'dir', 'role', 'style', 'hidden'))
_ATTRS = {
    'img': {'alt', 'width', 'height'}, 'input': {'type', 'value', 'placeholder', 'checked', 'size', 'min', 'max', 'step'},
    'textarea': {'rows', 'cols', 'placeholder'}, 'button': set(),
    'label': {'for'}, 'option': {'selected', 'label', 'value'}, 'optgroup': {'label'},
    'select': {'multiple', 'size'}, 'ol': {'start', 'reversed', 'type'}, 'li': {'value'},
    'th': {'scope', 'colspan', 'rowspan', 'headers'}, 'td': {'colspan', 'rowspan', 'headers'},
    'col': {'span'}, 'colgroup': {'span'}, 'details': {'open'}, 'time': {'datetime'},
    'progress': {'value', 'max'}, 'meter': {'value', 'min', 'max', 'low', 'high', 'optimum'},
}
_DEVICE = re.compile(r'(?:con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|\Z)', re.I)
_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def _fail():
    raise ValueError('invalid_pipeline_preview')


def _files(files, stage):
    if stage not in ('wireframe', 'frontend') or type(files) is not list or not 1 <= len(files) <= 64:
        _fail()
    result, total, seen = {}, 0, set()
    for item in files:
        if type(item) is not dict or item.keys() != {'path', 'content'}:
            _fail()
        path, content = item['path'], item['content']
        if type(path) is not str or len(path) > 240 or not path.startswith(stage + '/'):
            _fail()
        parts = path.split('/')
        if len(parts) > 12 or any(not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', part)
                                 or part.endswith('.') or _DEVICE.match(part) for part in parts):
            _fail()
        if path.casefold() in seen or type(content) is not str or '\x00' in content:
            _fail()
        try:
            size = len(content.encode('utf-8'))
        except UnicodeError:
            _fail()
        total += size
        if size > MAX_FILE_BYTES or total > MAX_BYTES:
            _fail()
        seen.add(path.casefold())
        result[path] = content
    if stage + '/index.html' not in result:
        _fail()
    return result


def _image(value):
    if type(value) is not str or len(value) > 131_072:
        return None
    match = re.fullmatch(r'data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/]*={0,2})', value, re.I)
    if not match:
        return None
    try:
        binary = base64.b64decode(match[2], validate=True)
    except ValueError:
        return None
    signatures = {'png': binary.startswith(b'\x89PNG\r\n\x1a\n'),
                  'jpeg': binary.startswith(b'\xff\xd8\xff'),
                  'webp': binary.startswith(b'RIFF') and binary[8:12] == b'WEBP'}
    return value if signatures[match[1].lower()] else None


class _Preview(HTMLParser):
    def __init__(self, files, stage):
        super().__init__(convert_charrefs=True)
        self.files, self.stage = files, stage
        self.output, self.css, self.stack = [], [], []
        self.blocked, self.in_head, self.in_style = [], False, False
        self.style_data, self.nodes = [], 0
        self.css_bytes, self.linked_css = 0, set()
        self.root_attributes, self.body_attributes = '', ''

    def _attrs(self, tag, attrs):
        result, seen = [], set()
        for key, value in attrs:
            if key in seen or key.startswith('on'):
                continue
            seen.add(key)
            allowed = key in _GLOBAL or key in _ATTRS.get(tag, ()) or re.fullmatch(r'(?:aria|data)-[a-z][a-z0-9-]*', key)
            if key == 'src' and tag in ('img', 'input'):
                value = _image(value)
                allowed = value is not None
            if not allowed or (value is not None and (len(value) > (131_072 if key == 'src' else 8192)
                                                       or _CONTROL.search(value))):
                continue
            if tag == 'input' and key == 'type' and value not in (
                    'text', 'email', 'tel', 'url', 'number', 'password', 'checkbox', 'radio',
                    'date', 'datetime-local', 'month', 'week', 'time', 'range', 'color', 'image', 'hidden'):
                value = 'button'
            result.append(' ' + key + ('' if value is None else '="' + escape(value, quote=True) + '"'))
        if tag in ('input', 'textarea', 'select', 'button'):
            result.append(' disabled')
        if tag == 'button':
            result.append(' type="button"')
        return ''.join(result)

    def _css_link(self, attrs):
        attrs = dict(attrs)
        if 'stylesheet' not in (attrs.get('rel') or '').lower().split():
            return
        try:
            url = urlsplit(attrs.get('href') or '')
        except ValueError:
            return
        if url.scheme or url.netloc:
            return
        path = unquote(url.path)
        if '\\' in path or '\x00' in path or '..' in path.split('/'):
            return
        target = posixpath.normpath(self.stage + '/' + path.lstrip('/'))
        if target.startswith(self.stage + '/') and target.endswith('.css') and target in self.files \
                and target not in self.linked_css:
            self.linked_css.add(target)
            self._add_css(self.files[target])

    def _add_css(self, content):
        self.css_bytes += len(content.encode('utf-8'))
        if self.css_bytes > MAX_BYTES:
            _fail()
        self.css.append(content)

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 20_000:
            _fail()
        if len(self.stack) + len(self.blocked) > 128:
            _fail()
        if self.blocked:
            if tag in _BLOCKED and tag != 'embed':
                self.blocked.append(tag)
            return
        if tag in _BLOCKED:
            if tag != 'embed':
                self.blocked.append(tag)
            return
        if tag == 'style':
            self.in_style, self.style_data = True, []
            return
        if tag == 'head':
            self.in_head = True
            return
        if tag == 'link':
            self._css_link(attrs)
            return
        if tag == 'html':
            self.root_attributes = self._attrs('html', attrs)
            return
        if tag == 'body':
            self.in_head = False
            self.body_attributes = self._attrs('body', attrs)
            return
        if self.in_head or tag in ('meta', 'base'):
            return
        output_tag = 'div' if tag == 'form' else tag
        if output_tag not in _ALLOWED:
            return
        self.output.append('<' + output_tag + self._attrs(output_tag, attrs) + '>')
        if output_tag not in _VOID:
            self.stack.append((tag, output_tag))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and tag not in ('meta', 'link', 'base', 'embed'):
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.blocked:
            if tag in self.blocked:
                index = len(self.blocked) - 1 - self.blocked[::-1].index(tag)
                del self.blocked[index:]
            return
        if tag == 'style':
            if self.in_style:
                self._add_css(''.join(self.style_data))
            self.in_style, self.style_data = False, []
            return
        if tag == 'head':
            self.in_head = False
            return
        if self.in_head:
            return
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                while len(self.stack) > index:
                    self.output.append('</' + self.stack.pop()[1] + '>')
                break

    def handle_data(self, data):
        if self.blocked:
            return
        if self.in_style:
            self.style_data.append(data)
        elif not self.in_head:
            self.output.append(escape(data))


def render_preview(files, stage='wireframe') -> bytes:
    """Return static presentation only; response and iframe sandboxing are required."""
    entries = _files(files, stage)
    parser = _Preview(entries, stage)
    try:
        parser.feed(entries[stage + '/index.html'])
        parser.close()
    except (ValueError, AssertionError, RecursionError):
        raise ValueError('invalid_pipeline_preview') from None
    while parser.stack:
        parser.output.append('</' + parser.stack.pop()[1] + '>')
    # CSS remains CSS text: a literal closing style tag must never leave its element.
    css = '\n'.join(parser.css).replace('<', '\\3c ')
    html = ('<!doctype html><html' + parser.root_attributes + '><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>정적 화면 미리보기</title><style>' + css + '</style></head>'
            '<body' + parser.body_attributes + '>' + ''.join(parser.output) + '</body></html>')
    result = html.encode('utf-8')
    if len(result) > 4 * MAX_BYTES:
        _fail()
    return result
