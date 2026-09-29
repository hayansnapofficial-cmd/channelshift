"""Expose implemented planning primitives without claiming completed builds."""
from .delivery_profile import standard_site_profile
from .project_catalog import SEO_CHECKS, DELIVERY_CHECKS, _https


SECTIONS = {
    'backend': {'title': 'API·백엔드', 'description': 'API 계약과 백엔드 구축에 필요한 설계·검수 기준입니다.',
                'stage_ids': ['domain_model', 'erd', 'database_schema', 'api_contract', 'backend_architecture',
                              'system_review', 'database_build', 'database_review', 'backend_build', 'backend_review'],
                'remaining': 'OpenAPI 편집, ERD와 API의 자동 정합성 검사, 서버 생성·실행은 아직 연결되지 않았습니다.'},
    'security': {'title': '보안 검수', 'description': '설계부터 납품까지 확인할 보안 항목입니다.',
                 'stage_ids': ['security_requirements', 'threat_model', 'authorization_policy', 'security_design_review',
                               'database_policy_tests', 'backend_security_tests', 'security_implementation_review', 'security_release_review'],
                 'remaining': '검사 실행기와 인증된 승인 절차는 아직 연결되지 않았습니다.'},
    'seo': {'title': 'SEO·공유 설정', 'description': '검색·공유 정보를 프로젝트 초안으로 저장하고 미리 봅니다.',
            'stage_ids': [], 'remaining': '초안 저장은 실제 홈페이지에 반영되지 않습니다. 이미지 접근·HTML·색인 검사는 아직 실행되지 않습니다.'},
    'delivery': {'title': '납품 검수', 'description': '배포와 운영 인계 전에 필요한 결과물을 확인합니다.',
                 'stage_ids': ['acceptance', 'release_review', 'deployment_approval', 'deployment', 'operations_security', 'handover'],
                 'remaining': '자동 배포와 납품 승인 집행은 아직 연결되지 않았습니다.'},
}

CHECK_LABELS = {
    'SEO_RENDERED_HTML': '실제 HTML의 검색·공유 메타 정보', 'SEO_URL_SAFETY': 'Canonical·HTTPS 주소',
    'SEO_OG_IMAGE_FETCH': '공유 이미지 접근·규격', 'SEO_SITEMAP_ROBOTS': '사이트맵·robots.txt',
    'SEO_REDIRECT_404': '리다이렉트·404', 'SEO_STRUCTURED_DATA': '구조화 데이터',
    'SEO_SEARCH_CONSOLE_READY': '검색 도구 연결', 'DELIVERY_OWNERSHIP': '도메인·계정 소유권',
    'DELIVERY_BACKUP_RESTORE': '백업·복원 확인', 'DELIVERY_SECRET_HANDOFF': '자격증명 인계',
    'DELIVERY_TEMP_ACCESS_REVOKED': '임시 접근 권한 회수', 'DELIVERY_CUSTOMER_ACCEPTANCE': '고객 인수 확인',
}


def profile():
    stages = {stage['id']: stage for stage in standard_site_profile()['stages']}
    sections = []
    for key, section in SECTIONS.items():
        checks = SEO_CHECKS if key == 'seo' else DELIVERY_CHECKS if key == 'delivery' else []
        sections.append({'id': key, **section,
                         'stages': [stages[item] for item in section['stage_ids'] if item in stages],
                         'checks': [{'id': item, 'title': CHECK_LABELS[item], 'status': 'not_run'} for item in checks],
                         'execution_enabled': False})
    return {'sections': sections}


def validate_settings(section, values):
    if section != 'seo' or type(values) is not dict or set(values) != {'site_name', 'title', 'description', 'url', 'image_url', 'noindex'}:
        raise ValueError('invalid_workbench_settings')
    for key, limit in [('site_name', 100), ('title', 200), ('description', 500), ('url', 2048), ('image_url', 2048)]:
        value = values[key]
        if type(value) is not str or len(value) > limit or any(ord(char) < 32 for char in value):
            raise ValueError('invalid_workbench_settings')
        try:
            value.encode('utf-8')
            if key in {'url', 'image_url'} and value:
                _https(value)
        except (ValueError, UnicodeError):
            raise ValueError('invalid_workbench_settings') from None
    if type(values['noindex']) is not bool:
        raise ValueError('invalid_workbench_settings')
    return dict(values)
