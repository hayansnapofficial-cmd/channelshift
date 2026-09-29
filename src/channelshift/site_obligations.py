"""Pure seven-item site disclosure baseline; completeness is not legal approval.

This module neither invents business facts/policy terms nor publishes files.
The same product baseline applies to sales, service and SaaS sites.
"""

from html import escape
import re
import unicodedata
from urllib.parse import quote


FORMAT = 'channelshift.site-obligations/v1'
REVIEWED_ON = '2026-09-29'
MAX_TOTAL_BYTES = 120_000
_SHAPE = {
    'company': ('name', 'representative', 'business_number', 'address'),
    'commerce': ('registration_number',),
    'contact': ('email', 'phone'),
    'hosting': ('name',),
    'policies': ('privacy', 'terms', 'refund'),
}
_FIELDS = {
    'company.name': ('상호', 160, False),
    'company.representative': ('대표자 성명', 80, False),
    'company.business_number': ('사업자등록번호', 20, False),
    'company.address': ('사업장 및 고객 불만 처리 주소', 500, False),
    'commerce.registration_number': ('통신판매업 신고번호', 160, False),
    'contact.email': ('고객문의 이메일', 254, False),
    'contact.phone': ('고객문의 전화번호', 40, False),
    'hosting.name': ('호스팅서비스 제공자 상호', 160, False),
    'policies.privacy': ('개인정보처리방침 본문', 12_000, True),
    'policies.terms': ('서비스이용약관 본문', 12_000, True),
    'policies.refund': ('취소환불규정 본문', 12_000, True),
}
_ITEMS = (
    ('business_info', '사업자정보', ('company.name', 'company.representative',
                                  'company.business_number', 'company.address')),
    ('privacy_policy', '개인정보처리방침', ('policies.privacy',)),
    ('commerce_info', '통신판매업정보', ('commerce.registration_number',)),
    ('customer_contact', '고객문의', ('contact.email', 'contact.phone')),
    ('hosting_provider', '호스팅사', ('hosting.name',)),
    ('terms_of_service', '서비스이용약관', ('policies.terms',)),
    ('refund_policy', '취소환불규정', ('policies.refund',)),
)
_SOURCES = (
    {
        'title': '전자상거래법 제10조: 사이버몰의 운영',
        'url': 'https://www.law.go.kr/LSW/lsLinkCommonInfo.do?lsJoLnkSeq=1022342373',
        'effective_on': '2026-07-21',
    },
    {
        'title': '전자상거래법 제13조: 신원 및 거래조건에 대한 정보의 제공',
        'url': 'https://law.go.kr/LSW/lsLawLinkInfo.do?chrClsCd=010202&lsJoLnkSeq=1013449713',
        'effective_on': '2026-07-21',
    },
    {
        'title': '전자상거래법 시행령 제11조의4: 사이버몰의 표시',
        'url': 'https://law.go.kr/LSW/lsLinkCommonInfo.do?lspttninfSeq=63473',
        'effective_on': '2026-07-21',
    },
    {
        'title': '개인정보 보호법 제30조: 개인정보 처리방침의 수립 및 공개',
        'url': 'https://www.law.go.kr/lsLinkCommonInfo.do?lsJoLnkSeq=1029331583',
        'effective_on': '2026-09-11',
    },
)
_EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+\Z")
_PHONE = re.compile(r'\+?[0-9][0-9 ()-]*\Z')
_BUSINESS_NUMBER = re.compile(r'(?:[0-9]{10}|[0-9]{3}-[0-9]{2}-[0-9]{5})\Z')
_INVISIBLE = frozenset('\u115f\u1160\u2800\u3164\uffa0')


def catalog() -> dict:
    """Return detached form metadata and blank drafts, with no invented facts."""
    return {
        'format': FORMAT,
        'site_types': ['sales', 'service', 'saas'],
        'items': [dict(id=key, label=label, fields=list(fields), required=True)
                  for key, label, fields in _ITEMS],
        'fields': {key: dict(label=label, max_length=limit, multiline=multiline)
                   for key, (label, limit, multiline) in _FIELDS.items()},
        'empty_values': {group: dict.fromkeys(fields, '') for group, fields in _SHAPE.items()},
        'reviewed_on': REVIEWED_ON,
        'sources': [dict(source, reviewed_on=REVIEWED_ON) for source in _SOURCES],
        'legal_review_required': True,
    }


def _text(value, limit: int, multiline: bool = False) -> str:
    if type(value) is not str or len(value) > limit:
        raise ValueError('invalid_site_obligations')
    for char in value:
        if char in '\r\n\t' and multiline:
            continue
        if unicodedata.category(char) in ('Cc', 'Cf', 'Cs') or char in _INVISIBLE:
            raise ValueError('invalid_site_obligations')
    value = value.replace('\r\n', '\n').replace('\r', '\n').strip()
    if value and not any(char.isalnum() for char in value):
        raise ValueError('invalid_site_obligations')
    return value


def validate(values: dict) -> dict:
    """Validate exact bounded scalar shape; blanks are valid unfinished drafts.

    Only syntax is checked for identifiers/contact details. Actual registration,
    contact ownership and legal sufficiency cannot be established here.
    """
    if type(values) is not dict or values.keys() != _SHAPE.keys():
        raise ValueError('invalid_site_obligations')
    result = {}
    total = 0
    for group, fields in _SHAPE.items():
        section = values[group]
        if type(section) is not dict or section.keys() != set(fields):
            raise ValueError('invalid_site_obligations')
        result[group] = {}
        for field in fields:
            _, limit, multiline = _FIELDS[f'{group}.{field}']
            value = _text(section[field], limit, multiline)
            total += len(value.encode('utf-8'))
            if total > MAX_TOTAL_BYTES:
                raise ValueError('invalid_site_obligations')
            result[group][field] = value
    email, phone = result['contact']['email'], result['contact']['phone']
    business_number = result['company']['business_number']
    if email and (not _EMAIL.fullmatch(email) or '..' in email.split('@')[0]
                  or email.startswith('.') or '.@' in email):
        raise ValueError('invalid_site_obligations')
    if phone and (not _PHONE.fullmatch(phone)
                  or not 7 <= sum(c in '0123456789' for c in phone) <= 15):
        raise ValueError('invalid_site_obligations')
    if business_number and not _BUSINESS_NUMBER.fullmatch(business_number):
        raise ValueError('invalid_site_obligations')
    return result


def _at(values: dict, path: str) -> str:
    group, field = path.split('.')
    return values[group][field]


def assess(values: dict) -> dict:
    """Report product completeness for all seven items; never grant legal approval."""
    checked = validate(values)
    missing_fields = [path for path in _FIELDS if not _at(checked, path)]
    missing_items = [key for key, _, fields in _ITEMS
                     if any(not _at(checked, path) for path in fields)]
    return {'ready': not missing_items, 'missing_items': missing_items,
            'missing_fields': missing_fields, 'complete_count': len(_ITEMS) - len(missing_items),
            'required_count': len(_ITEMS), 'legal_review_required': True}


def render_pages(values: dict, site_name: str) -> list[dict[str, str]]:
    """Return complete, escaped local HTML assets without writing or publishing.

    Paths are relative export paths; generated navigation uses fixed root paths.
    Every complete page embeds the same seven-item footer. Policy text is literal
    text, never interpreted as HTML or Markdown.
    """
    checked = validate(values)
    name = _text(site_name, 200)
    if not name:
        raise ValueError('invalid_site_obligations')
    if not assess(checked)['ready']:
        raise ValueError('site_obligations_incomplete')
    value = lambda path: escape(_at(checked, path), quote=True)
    email = escape('mailto:' + quote(checked['contact']['email'], safe='@._+-'), quote=True)
    phone_number = re.sub(r'[^+0-9]', '', checked['contact']['phone'])
    phone = escape('tel:' + phone_number, quote=True)
    footer = (
        '<footer aria-label="사이트 필수 정보">\n'
        '<section aria-label="사업자정보"><h2>사업자정보</h2><dl>\n'
        f'<dt>상호</dt><dd>{value("company.name")}</dd>\n'
        f'<dt>대표자 성명</dt><dd>{value("company.representative")}</dd>\n'
        f'<dt>사업자등록번호</dt><dd>{value("company.business_number")}</dd>\n'
        f'<dt>사업장 및 고객 불만 처리 주소</dt><dd>{value("company.address")}</dd>\n'
        '</dl></section>\n'
        '<section aria-label="통신판매업정보"><h2>통신판매업정보</h2>'
        f'<p>통신판매업 신고번호: {value("commerce.registration_number")}</p></section>\n'
        '<section aria-label="고객문의"><h2><a href="/contact.html">고객문의</a></h2>'
        f'<p>이메일: <a href="{email}">{value("contact.email")}</a></p>'
        f'<p>전화번호: <a href="{phone}">{value("contact.phone")}</a></p></section>\n'
        '<section aria-label="호스팅사"><h2>호스팅사</h2>'
        f'<p>호스팅서비스 제공자: {value("hosting.name")}</p></section>\n'
        '<nav aria-label="정책 안내"><ul>\n'
        '<li><a href="/privacy.html">개인정보처리방침</a></li>\n'
        '<li><a href="/terms.html">서비스이용약관</a></li>\n'
        '<li><a href="/refund.html">취소환불규정</a></li>\n'
        '</ul></nav>\n</footer>\n'
    )

    def page(title: str, body: str) -> str:
        return ('<!doctype html>\n<html lang="ko"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                f'<title>{escape(title)} | {escape(name)}</title></head><body>\n'
                f'<header><a href="/">{escape(name)}</a></header>\n'
                f'<main><h1>{escape(title)}</h1>{body}</main>\n{footer}</body></html>\n')

    files = [{'path': 'footer.html', 'content': footer}]
    for field, title in (('privacy', '개인정보처리방침'), ('terms', '서비스이용약관'),
                         ('refund', '취소환불규정')):
        paragraphs = ''.join('<p>' + escape(part).replace('\n', '<br>') + '</p>\n'
                             for part in checked['policies'][field].split('\n\n'))
        files.append({'path': f'{field}.html', 'content': page(title, paragraphs)})
    files.append({'path': 'contact.html', 'content': page('고객문의',
                 f'<p><a href="{email}">{value("contact.email")}</a></p>'
                 f'<p><a href="{phone}">{value("contact.phone")}</a></p>')})
    return files
