"""Static preview sanitization; no generated application execution or network."""
import base64
from html.parser import HTMLParser
import unittest

from channelshift.pipeline_preview import render_preview


class Document(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.tags, self.attrs, self.text = [], [], []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs.extend((tag, key, value) for key, value in attrs)

    def handle_data(self, text):
        self.text.append(text)


def files(html, css=None, stage='wireframe'):
    result = [{'path': stage + '/index.html', 'content': html}]
    if css is not None:
        result.append({'path': stage + '/style.css', 'content': css})
    return result


class PipelinePreviewTests(unittest.TestCase):
    def test_local_css_inline_styles_and_presentational_html_preserved(self):
        value = files('<html lang="ko"><head><link rel="stylesheet" href="/style.css">'
                      '<style>.card { padding: 10px; }</style></head><body class="page">'
                      '<main><h1>문의</h1><section class="card" style="color:red">내용</section>'
                      '</main></body></html>', 'body { background: white; }')
        result = render_preview(value).decode()
        parsed = Document(result)
        self.assertIn('body { background: white; }', result)
        self.assertIn('.card { padding: 10px; }', result)
        self.assertIn(('body', 'class', 'page'), parsed.attrs)
        self.assertIn(('html', 'lang', 'ko'), parsed.attrs)
        self.assertIn(('section', 'style', 'color:red'), parsed.attrs)
        self.assertIn('문의', ''.join(parsed.text))
        self.assertNotIn('link', parsed.tags)

    def test_active_elements_subtrees_navigation_events_and_refresh_removed(self):
        value = files('<html><head><base href="https://outside.invalid/">'
                      '<meta http-equiv="refresh" content="0;url=https://outside.invalid/"></head><body>'
                      '<script>privateScript()</script><iframe src="https://outside.invalid/">hidden-frame</iframe>'
                      '<object data="https://outside.invalid/"><p>hidden-object</p></object>'
                      '<embed src="https://outside.invalid/"><svg onload="bad()"><script>bad()</script></svg>'
                      '<a href="javascript:bad()" target="_top" ping="https://outside.invalid/" onclick="bad()">이동</a>'
                      '<p onmouseover="bad()">보이는 내용</p></body></html>')
        result = render_preview(value).decode()
        parsed = Document(result)
        self.assertTrue(set(parsed.tags).isdisjoint({'script', 'iframe', 'object', 'embed', 'base', 'svg'}))
        self.assertFalse(any(key.startswith('on') or key in {'href', 'ping', 'target', 'http-equiv'} for _, key, _ in parsed.attrs))
        self.assertNotIn('privateScript', result)
        self.assertNotIn('hidden-frame', result)
        self.assertNotIn('hidden-object', result)
        self.assertIn('보이는 내용', result)

    def test_forms_are_static_and_all_editable_controls_disabled(self):
        result = render_preview(files('<body><form action="https://outside.invalid/" method="post">'
            '<label for="name">이름</label><input id="name" name="secret" autofocus value="테스트">'
            '<textarea name="message">내용</textarea><select><option>선택</option></select>'
            '<button type="submit" formaction="https://outside.invalid/">전송</button>'
            '<input type="submit" value="신청"></form></body>')).decode()
        parsed = Document(result)
        self.assertNotIn('form', parsed.tags)
        self.assertFalse(any(key in {'action', 'method', 'formaction', 'autofocus', 'name'}
                             for tag, key, _ in parsed.attrs if tag != 'meta'))
        for tag in ('input', 'textarea', 'select', 'button'):
            self.assertIn((tag, 'disabled', None), parsed.attrs)
        self.assertIn(('button', 'type', 'button'), parsed.attrs)
        self.assertIn(('input', 'type', 'button'), parsed.attrs)

    def test_only_signature_matching_raster_data_images_survive(self):
        png = 'data:image/png;base64,' + base64.b64encode(b'\x89PNG\r\n\x1a\nsynthetic').decode()
        jpeg = 'data:image/jpeg;base64,' + base64.b64encode(b'\xff\xd8\xffsynthetic').decode()
        webp = 'data:image/webp;base64,' + base64.b64encode(b'RIFF0000WEBPsynthetic').decode()
        bad = ['https://outside.invalid/a.png', '/local.png', 'data:image/svg+xml;base64,PHN2Zz4=',
               'data:image/png;base64,' + base64.b64encode(b'<svg onload="bad()">').decode(),
               'data:image/png;base64,not-base64']
        markup = '<body>' + ''.join('<img src="' + src + '" srcset="https://outside.invalid/2.png">' for src in [png, jpeg, webp] + bad) + '</body>'
        parsed = Document(render_preview(files(markup)).decode())
        self.assertEqual([value for _, key, value in parsed.attrs if key == 'src'], [png, jpeg, webp])
        self.assertFalse(any(key == 'srcset' for _, key, _ in parsed.attrs))

    def test_css_cannot_break_out_of_style_element(self):
        result = render_preview(files('<body><link rel="stylesheet" href="style.css"><h1>안내</h1></body>',
            'p::after {content:"</style><script>alert(1)</script>";} @import "https://outside.invalid/x.css";')).decode()
        parsed = Document(result)
        self.assertNotIn('script', parsed.tags)
        self.assertEqual(parsed.tags.count('style'), 1)
        self.assertIn('\\3c /style>', result)
        # External CSS URLs remain inert text; the required response CSP denies requests.
        self.assertIn('@import', result)

    def test_external_missing_and_traversing_stylesheets_are_not_loaded(self):
        result = render_preview(files('<head><link rel="stylesheet" href="https://outside.invalid/style.css">'
            '<link rel="stylesheet" href="../style.css"><link rel="stylesheet" href="missing.css"></head>'
            '<body>보기</body>', 'should_not_appear')).decode()
        self.assertNotIn('should_not_appear', result)
        self.assertNotIn('outside.invalid', result)

    def test_frontend_script_content_and_all_anchor_hrefs_removed(self):
        value = files('<html><body><a href="/privacy.html">개인정보</a>'
            '<a href="#local">본문</a><script src="app.js"></script><p>페이지</p></body></html>', stage='frontend')
        value.append({'path': 'frontend/app.js', 'content': 'throw new Error("must-not-run");'})
        result = render_preview(value, 'frontend').decode()
        self.assertNotIn('script', Document(result).tags)
        self.assertNotIn('must-not-run', result)
        self.assertFalse(any(key == 'href' for _, key, _ in Document(result).attrs))
        self.assertIn('개인정보', result)

    def test_text_and_attributes_are_escaped_and_comments_not_executable(self):
        result = render_preview(files('<body><p title="&quot; onmouseover=&quot;bad()">'
            '&lt;script&gt;보이는 텍스트&lt;/script&gt;</p><!-- comment --></body>')).decode()
        parsed = Document(result)
        self.assertNotIn('script', parsed.tags)
        self.assertFalse(any(key.startswith('on') for _, key, _ in parsed.attrs))
        self.assertIn('&lt;script&gt;', result)

    def test_repeated_css_link_is_bounded_and_preview_deterministic(self):
        value = files('<head>' + '<link rel="stylesheet" href="/style.css">' * 1000 + '</head><body>페이지</body>',
                      '/* layout */ .page { color: #111; }')
        first = render_preview(value)
        self.assertEqual(first, render_preview(value))
        self.assertEqual(first.decode().count('/* layout */'), 1)

    def test_input_stage_paths_content_and_depth_bounded(self):
        for value, stage in ((files('<body>x</body>'), 'backend'), ([], 'wireframe'),
            ([{'path': 'wireframe/../index.html', 'content': 'x'}], 'wireframe'),
            ([{'path': 'wireframe/index.html', 'content': '\ud800'}], 'wireframe'),
            (files('<div>' * 140 + 'x' + '</div>' * 140), 'wireframe'),
            (files('<body>x</body>') + [{'path': 'wireframe/INDEX.html', 'content': 'x'}], 'wireframe')):
            with self.subTest(stage=stage):
                with self.assertRaisesRegex(ValueError, '^invalid_pipeline_preview$'):
                    render_preview(value, stage)


if __name__ == '__main__':
    unittest.main()
