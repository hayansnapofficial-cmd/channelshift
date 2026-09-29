"""Pure disclosure readiness/rendering checks using synthetic fixture values only."""

from copy import deepcopy
from html.parser import HTMLParser
import unittest

from channelshift.site_obligations import assess, catalog, render_pages, validate


def complete():
    return {
        'company': {'name': '테스트용 가상회사', 'representative': '테스트 대표',
                    'business_number': '000-00-00000', 'address': '테스트 전용 주소'},
        'commerce': {'registration_number': '테스트 전용 신고번호'},
        'contact': {'email': 'test@example.invalid', 'phone': '010-0000-0000'},
        'hosting': {'name': '테스트용 호스팅사'},
        'policies': {'privacy': '테스트용 개인정보 본문입니다.',
                     'terms': '테스트용 이용약관 본문입니다.',
                     'refund': '테스트용 환불규정 본문입니다.'},
    }


class Tags(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.tags = []
        self.links = []
        self.attributes = []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend(attrs)
        self.links.extend(value for key, value in attrs if key == 'href')


class SiteObligationsTests(unittest.TestCase):
    def test_catalog_is_seven_mandatory_items_for_every_type_without_defaults(self):
        forms = catalog()
        self.assertEqual(forms['site_types'], ['sales', 'service', 'saas'])
        self.assertEqual([x['label'] for x in forms['items']], [
            '사업자정보', '개인정보처리방침', '통신판매업정보', '고객문의', '호스팅사',
            '서비스이용약관', '취소환불규정'])
        self.assertTrue(all(x['required'] for x in forms['items']))
        self.assertTrue(all(value == '' for section in forms['empty_values'].values()
                            for value in section.values()))
        self.assertEqual(len(forms['fields']), 11)
        self.assertTrue(all(source['url'].startswith('https://')
                            and 'law.go.kr/' in source['url'] for source in forms['sources']))
        self.assertTrue(all(source['reviewed_on'] == forms['reviewed_on']
                            for source in forms['sources']))

    def test_blank_drafts_allowed_but_all_seven_missing(self):
        values = catalog()['empty_values']
        self.assertEqual(validate(values), values)
        status = assess(values)
        self.assertFalse(status['ready'])
        self.assertEqual(status['missing_items'], [item['id'] for item in catalog()['items']])
        self.assertEqual(len(status['missing_fields']), 11)
        self.assertEqual(status['complete_count'], 0)
        self.assertEqual(status['required_count'], 7)
        with self.assertRaisesRegex(ValueError, '^site_obligations_incomplete$'):
            render_pages(values, '테스트 사이트')

    def test_every_field_required_even_with_other_sections_filled(self):
        for item in catalog()['items']:
            for path in item['fields']:
                with self.subTest(path=path):
                    values = complete()
                    group, field = path.split('.')
                    values[group][field] = '   '
                    status = assess(values)
                    self.assertFalse(status['ready'])
                    self.assertEqual(status['missing_items'], [item['id']])
                    self.assertEqual(status['missing_fields'], [path])
                    self.assertEqual(status['complete_count'], 6)

    def test_ready_never_grants_legal_review_or_approvals(self):
        status = assess(complete())
        self.assertTrue(status['ready'])
        self.assertTrue(status['legal_review_required'])
        self.assertEqual(status['complete_count'], 7)
        self.assertEqual(status['missing_fields'], [])
        self.assertNotIn('approved', status)
        self.assertNotIn('published', status)

    def test_no_omitted_sections_extra_flags_or_nonstring_scalars(self):
        cases = [None, [], {}, {'ready': True}]
        for group in complete():
            missing = complete()
            del missing[group]
            cases.append(missing)
        for value in (None, True, 1, [], {'reviewed': True}):
            changed = complete()
            changed['commerce']['registration_number'] = value
            cases.append(changed)
        extra = complete()
        extra['commerce']['exempt'] = True
        cases.append(extra)
        extra = complete()
        extra['legal_review_required'] = False
        cases.append(extra)
        for values in cases:
            with self.subTest(values=values):
                with self.assertRaisesRegex(ValueError, '^invalid_site_obligations$'):
                    validate(values)

    def test_format_validation_does_not_echo_bad_contact_or_business_input(self):
        for field, values in (
            ('contact.email', ['javascript:alert(1)', 'a@example.invalid?subject=secret',
                               'a@example.invalid\r\nBcc:secret', 'x..y@example.invalid',
                               '.test@example.invalid', 'test.@example.invalid']),
            ('contact.phone', ['javascript:alert(1)', '12', '+82-10-0000-0000;ext=9',
                               '1234567890123456', '010-0000-0000" onclick="secret']),
            ('company.business_number', ['해당없음', '000-000-00000', '123456789']),
        ):
            group, key = field.split('.')
            for bad in values:
                with self.subTest(field=field, bad=bad):
                    values = complete()
                    values[group][key] = bad
                    with self.assertRaisesRegex(ValueError, '^invalid_site_obligations$'):
                        validate(values)

    def test_per_field_and_total_utf8_limits(self):
        for path, metadata in catalog()['fields'].items():
            values = complete()
            group, field = path.split('.')
            values[group][field] = '가' * (metadata['max_length'] + 1)
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, '^invalid_site_obligations$'):
                    validate(values)
        values = complete()
        values['policies'] = {key: '가' + '\U0001f600' * 11_999 for key in values['policies']}
        with self.assertRaisesRegex(ValueError, '^invalid_site_obligations$'):
            validate(values)

    def test_whitespace_normalization_and_invisible_empty_bypass(self):
        values = complete()
        values['policies']['privacy'] = ' \r\n\t '\
            '\u2003'
        self.assertIn('privacy_policy', assess(values)['missing_items'])
        for bad in ('\u200b', '\ufeff', '\u3164', '\u2800', '\u202e내용', '\x00내용', '\ud800'):
            values = complete()
            values['policies']['privacy'] = bad
            with self.subTest(bad=repr(bad)):
                with self.assertRaisesRegex(ValueError, '^invalid_site_obligations$'):
                    validate(values)
        values = complete()
        values['policies']['privacy'] = ' 첫 문단\r\n다음 줄\r\n '
        self.assertEqual(validate(values)['policies']['privacy'], '첫 문단\n다음 줄')

    def test_detached_results_and_no_input_mutation(self):
        values = complete()
        original = deepcopy(values)
        copy = validate(values)
        copy['company']['name'] = '수정'
        self.assertEqual(values, original)
        form = catalog()
        form['empty_values']['company']['name'] = '기본값 삽입'
        form['items'][0]['fields'].clear()
        form['sources'][0]['url'] = '변경'
        self.assertEqual(catalog()['empty_values']['company']['name'], '')
        self.assertEqual(len(catalog()['items'][0]['fields']), 4)
        self.assertTrue(catalog()['sources'][0]['url'].startswith('https://'))
        render_pages(values, '테스트 사이트')
        self.assertEqual(values, original)

    def test_render_has_fixed_files_and_seven_visible_entries(self):
        files = render_pages(complete(), '테스트 사이트')
        self.assertEqual([f['path'] for f in files], [
            'footer.html', 'privacy.html', 'terms.html', 'refund.html', 'contact.html'])
        for file in files:
            with self.subTest(path=file['path']):
                for item in catalog()['items']:
                    self.assertIn(item['label'], file['content'])
                for target in ('/privacy.html', '/terms.html', '/refund.html', '/contact.html'):
                    self.assertIn(target, Tags(file['content']).links)
        self.assertIn(complete()['policies']['privacy'], files[1]['content'])
        self.assertIn(complete()['policies']['terms'], files[2]['content'])
        self.assertIn(complete()['policies']['refund'], files[3]['content'])

    def test_render_escapes_html_and_only_fixed_or_safe_contact_links(self):
        values = complete()
        values['company']['name'] = '<script>alert("company")</script>'
        values['policies']['privacy'] = '<img src=x onerror=alert(1)>\n<script>secret</script>'
        values['contact']['email'] = 'test?subject@example.invalid'
        values['contact']['phone'] = '+82 (10) 0000-0000'
        files = render_pages(values, '<img src=x onerror=alert("site")>')
        for file in files:
            parsed = Tags(file['content'])
            self.assertNotIn('script', parsed.tags)
            self.assertNotIn('img', parsed.tags)
            self.assertFalse(any(name.startswith('on') for name, _ in parsed.attributes))
            self.assertTrue(set(parsed.links) <= {'/', '/privacy.html', '/terms.html',
                '/refund.html', '/contact.html', 'mailto:test%3Fsubject@example.invalid',
                'tel:+821000000000'})
            self.assertIn('&lt;script&gt;', file['content'])
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;<br>', files[1]['content'])

    def test_invalid_site_name_refuses_render(self):
        for name in ('', ' ', '가' * 201, None, '\x00사이트'):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, '^invalid_site_obligations$'):
                    render_pages(complete(), name)

    def test_site_name_200_characters_allowed_without_changing_company_limit(self):
        name = '가' * 200
        files = render_pages(complete(), name)
        self.assertIn(name, files[1]['content'])
        self.assertEqual(catalog()['fields']['company.name']['max_length'], 160)


if __name__ == '__main__':
    unittest.main()
