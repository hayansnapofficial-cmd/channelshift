"""Versioned, deterministic feature choices, separate from customer source text.

The catalog offers bounded product decisions, never grants an approval or
activates a service. Projects retain their original catalog snapshot.
"""
from __future__ import annotations

import copy

CATALOG_VERSION = 'channelshift.feature-guidance/v1'
MAX_NOTE = 1000
UNRESOLVED = {None, 'unsure', 'later'}


def _option(identifier, label, description):
    return {'id': identifier, 'label': label, 'description': description}


def _card(identifier, title, description, why, effort, options):
    return {'id': identifier, 'title': title, 'description': description, 'why': why,
            'effort': effort, 'options': options + [
                _option('unsure', '잘 모르겠어요', '내 Codex가 현재 요구와 설명을 바탕으로 추천합니다. 추천은 직접 선택해 반영합니다.'),
                _option('later', '나중에 결정', '선택을 보관하고 요구사양 확정 전에 다시 확인합니다.')]}


_CARDS = [
    _card('contact', '고객 문의는 어떻게 받을까요?', '방문자가 연락할 방법을 정합니다.',
          '문의 경로가 없으면 방문자가 서비스를 이용하기 어렵습니다.', '외부 연결은 적음 · 문의 저장과 관리 화면은 보통', [
              _option('form', '문의 폼', '사이트에서 문의를 받고 담당자가 관리합니다.'),
              _option('external', '외부 연락처 연결', '카카오톡·이메일 등 안내한 연락처로 연결합니다.'),
              _option('both', '문의 폼과 외부 연결', '사이트 문의와 외부 연락처를 함께 제공합니다.'),
              _option('none', '추가 문의 기능 없음', '별도 문의 기능을 만들지 않습니다. 필수 고객문의 안내는 유지합니다.')]),
    _card('booking', '예약은 어떻게 받고 싶으세요?', '예약이 필요하면 확정 방식을 선택합니다.',
          '예약 요청과 즉시 확정은 필요한 데이터와 운영 방식이 다릅니다.', '요청 접수는 보통 · 일정과 정원 관리는 많음', [
              _option('request', '예약 요청만 받기', '고객이 요청을 보내고 담당자가 따로 안내합니다.'),
              _option('approval', '담당자 확인 후 확정', '가능한 날짜를 요청받아 담당자가 승인하거나 거절합니다.'),
              _option('instant', '날짜 선택·즉시 확정', '일정과 정원을 확인하고 예약을 바로 확정합니다.'),
              _option('none', '예약 기능 없음', '예약 데이터와 예약 화면을 만들지 않습니다.')]),
    _card('accounts', '방문자 회원가입이 필요한가요?', '제작 도구 계정과 별개로 완성할 사이트의 회원 기능을 정합니다.',
          '회원 기능은 접근 권한과 개인정보 관리 범위를 늘립니다.', '비회원은 적음 · 역할별 회원 권한은 많음', [
              _option('members', '회원가입·로그인', '이메일 인증과 로그인, 본인 정보 관리가 필요합니다.'),
              _option('roles', '역할별 회원 권한', '회원·담당자 등 역할에 따라 접근 가능한 화면과 데이터를 나눕니다.'),
              _option('none', '방문자 회원가입 없음', '방문자는 가입 없이 이용합니다. 운영자 접근 보호는 별도로 유지합니다.')]),
    _card('payment', '사이트에서 결제를 받을까요?', '결제 기능과 취소·환불 흐름을 함께 정합니다.',
          '결제는 외부 서비스 연결과 실패·취소 처리 검수가 필요합니다.', '안내는 적음 · 결제 연동은 많음 · 정기결제는 더 많음', [
              _option('one_time', '일회성 결제', '주문별 결제를 받고 결제 결과와 환불 상태를 관리합니다.'),
              _option('subscription', '정기결제', '구독 주기, 해지, 결제 실패와 재시도 정책을 정합니다.'),
              _option('external', '외부 결제 안내', '계약된 외부 결제 페이지나 결제 방법을 안내합니다.'),
              _option('none', '결제 기능 없음', '사이트에서 결제를 처리하지 않습니다.')]),
    _card('content', '내용은 누가 어떻게 수정할까요?', '납품 후 글과 사진을 바꾸는 방법을 정합니다.',
          '업데이트 빈도에 맞는 관리 화면이 있어야 운영이 편합니다.', '고정 내용은 적음 · 게시물과 사진 관리는 보통', [
              _option('pages', '관리자가 페이지 수정', '운영자가 제목·소개·대표 사진을 관리 화면에서 수정합니다.'),
              _option('posts', '블로그·공지·자료 관리', '게시물과 분류를 등록하고 공개 여부를 관리합니다.'),
              _option('gallery', '사진·포트폴리오 관리', '사진과 작품 설명, 노출 순서를 관리합니다.'),
              _option('none', '고정 내용으로 시작', '별도 콘텐츠 관리 화면 없이 제작자가 내용을 수정합니다.')]),
    _card('notifications', '새 문의나 예약을 어떻게 알릴까요?', '알림을 받을 사람과 채널을 정합니다.',
          '발송 채널마다 인증, 비용, 실패 처리 방식이 다릅니다.', '화면 알림은 보통 · 외부 메시지 연결과 운영은 많음', [
              _option('dashboard', '관리 화면에서 확인', '외부 발송 없이 관리 화면의 새 항목을 확인합니다.'),
              _option('email', '이메일 알림', '연결한 발송 계정으로 필요한 업무 알림을 보냅니다.'),
              _option('message', '문자·메신저 알림', '계약한 메시지 서비스와 발송 조건을 추가로 확인합니다.'),
              _option('none', '추가 알림 없음', '자동 알림을 만들지 않습니다.')]),
    _card('design', '어떤 방식으로 디자인을 정할까요?', '대표 화면을 먼저 확인하고 전체 페이지로 확장합니다.',
          '색상·글꼴·간격과 주요 화면 방향을 먼저 맞추면 품질 편차를 줄일 수 있습니다.', '공통 기준 활용은 보통 · 별도 시안은 많음', [
              _option('standard', '공통 디자인 기준으로 시작', '정돈된 공통 구성으로 대표 화면을 만든 뒤 브랜드에 맞게 조정합니다.'),
              _option('reference', '참고 사이트를 비교해 결정', '참고 자료의 장단점을 검토하고 독자적인 화면 방향을 정합니다.'),
              _option('custom', '별도 디자인 방향 제안', '고객의 브랜드와 콘텐츠에 맞춘 시안을 먼저 검토합니다.')]),
    _card('seo', '검색·SNS 공유는 어디까지 준비할까요?', '기본 검색·공유 설정은 모든 사이트에 포함합니다.',
          '제목·설명·공유 이미지·사이트맵 누락을 납품 전에 확인합니다.', '기본 설정은 보통 · 콘텐츠별 확장은 많음', [
              _option('basic', '기본 검색·공유 설정', '제목·설명·대표 공유 이미지·사이트맵·로봇 정책 등 기본 항목을 준비합니다.'),
              _option('advanced', '페이지·콘텐츠별 확장', '기본 설정에 페이지별 메타데이터와 구조화 데이터를 추가 검토합니다.')]),
]


def catalog():
    """Detached server-owned definition to freeze at project creation."""
    return {'catalog_version': CATALOG_VERSION, 'cards': copy.deepcopy(_CARDS)}


def _snapshot(value):
    # The stored snapshot is the project contract, not whichever catalog happens
    # to be shipped later. Explicitly reject unknown formats instead of migration.
    if type(value) is not dict or set(value) != {'catalog_version', 'cards'} \
            or value['catalog_version'] != CATALOG_VERSION:
        raise ValueError('feature_guidance_unavailable')
    cards = value['cards']
    if type(cards) is not list or len(cards) != 8:
        raise ValueError('feature_guidance_unavailable')
    identifiers = set()
    for card in cards:
        if type(card) is not dict or set(card) != {'id', 'title', 'description', 'why', 'effort', 'options'}:
            raise ValueError('feature_guidance_unavailable')
        if any(type(card[key]) is not str or not card[key] or len(card[key]) > 500
               for key in ('id', 'title', 'description', 'why', 'effort')) or card['id'] in identifiers:
            raise ValueError('feature_guidance_unavailable')
        identifiers.add(card['id'])
        options = card['options']
        if type(options) is not list or not 3 <= len(options) <= 8:
            raise ValueError('feature_guidance_unavailable')
        option_ids = set()
        for option in options:
            if type(option) is not dict or set(option) != {'id', 'label', 'description'} \
                    or any(type(v) is not str or not v or len(v) > 500 for v in option.values()) \
                    or option['id'] in option_ids:
                raise ValueError('feature_guidance_unavailable')
            option_ids.add(option['id'])
        if not {'unsure', 'later'} <= option_ids or (card['id'] == 'seo' and 'none' in option_ids):
            raise ValueError('feature_guidance_unavailable')
    if identifiers != {card['id'] for card in _CARDS}:
        raise ValueError('feature_guidance_unavailable')
    return cards


def validate_decisions(snapshot, decisions):
    cards = {card['id']: card for card in _snapshot(snapshot)}
    if type(decisions) is not list or not 1 <= len(decisions) <= len(cards):
        raise ValueError('invalid_feature_decisions')
    result, seen = [], set()
    for item in decisions:
        if type(item) is not dict or set(item) != {'id', 'option', 'note'}:
            raise ValueError('invalid_feature_decisions')
        identifier, option, note = item['id'], item['option'], item['note']
        if type(identifier) is not str or identifier not in cards or identifier in seen:
            raise ValueError('invalid_feature_decisions')
        if option is not None and (type(option) is not str or
                                  option not in {row['id'] for row in cards[identifier]['options']}):
            raise ValueError('invalid_feature_decisions')
        if type(note) is not str or len(note) > MAX_NOTE \
                or any((ord(c) < 32 and c not in '\r\n\t') or 0xD800 <= ord(c) <= 0xDFFF for c in note):
            raise ValueError('invalid_feature_decisions')
        result.append({'id': identifier, 'option': option, 'note': note.strip()})
        seen.add(identifier)
    return result


def view(snapshot=None, decisions=None):
    if snapshot is None:
        return {'enabled': False, 'catalog_version': None, 'cards': [], 'unresolved': []}
    cards = copy.deepcopy(_snapshot(snapshot))
    selections = {row['id']: row for row in validate_decisions(snapshot, decisions)} if decisions else {}
    unresolved = []
    for card in cards:
        choice = selections.get(card['id'], {})
        card.update(selected=choice.get('option'), note=choice.get('note', ''))
        if card['selected'] in UNRESOLVED:
            unresolved.append(card['id'])
    return {'enabled': True, 'catalog_version': snapshot['catalog_version'],
            'cards': cards, 'unresolved': unresolved}


def decisions(guidance):
    return [{'id': card['id'], 'option': card['selected'], 'note': card['note']}
            for card in guidance['cards']]


def validate_recommendations(snapshot, frozen_decisions, recommendations):
    """Bind advice to the exact unsure choices the operator sent for analysis.

    A global catalog check is insufficient: the model may only recommend a
    concrete option for an explicitly unsure card in this project's catalog.
    Excluded, deferred, unanswered and already selected cards are not targets.
    """
    if type(recommendations) is not list or len(recommendations) > 8:
        raise ValueError('invalid_feature_recommendations')
    if not recommendations:
        return
    if type(snapshot) is not dict or type(frozen_decisions) is not dict \
            or set(frozen_decisions) != {'catalog_version', 'decisions'} \
            or frozen_decisions['catalog_version'] != snapshot.get('catalog_version'):
        raise ValueError('invalid_feature_recommendations')
    cards = {card['id']: card for card in _snapshot(snapshot)}
    selected = {row['id']: row['option'] for row in validate_decisions(snapshot, frozen_decisions['decisions'])}
    if selected.keys() != cards.keys():
        raise ValueError('invalid_feature_recommendations')
    seen = set()
    for recommendation in recommendations:
        if type(recommendation) is not dict \
                or set(recommendation) != {'card_id', 'option_id', 'reason', 'tradeoff'}:
            raise ValueError('invalid_feature_recommendations')
        identifier, option = recommendation['card_id'], recommendation['option_id']
        if type(identifier) is not str or identifier not in cards or identifier in seen \
                or selected[identifier] != 'unsure' or type(option) is not str \
                or option in UNRESOLVED or option not in {row['id'] for row in cards[identifier]['options']}:
            raise ValueError('invalid_feature_recommendations')
        for key in ('reason', 'tradeoff'):
            text = recommendation[key]
            if type(text) is not str or not text.strip() or len(text) > 500 \
                    or any((ord(c) < 32 and c not in '\r\n\t') or 0xD800 <= ord(c) <= 0xDFFF for c in text):
                raise ValueError('invalid_feature_recommendations')
        seen.add(identifier)


def input_text(guidance):
    """Only current operator decisions enter analysis; old choices stay in history."""
    if not guidance['enabled']:
        return ''
    lines = ['[기능 선택 · 작업자 입력 · 고객 원문과 구분]',
             '카탈로그: ' + guidance['catalog_version'],
             '아래는 현재 선택입니다. 이전 선택을 대체하며, 조사 결과나 자동 추천은 아닙니다.',
             '선택과 설명 또는 고객 원문이 충돌하면 확인 질문으로 해결하고 임의로 확정하지 마세요.']
    for card in guidance['cards']:
        option = next((item for item in card['options'] if item['id'] == card['selected']), None)
        lines.append(card['id'] + ' · ' + card['title'])
        lines.append('선택: ' + (option['label'] + ' — ' + option['description'] if option else '미선택'))
        if card['note']:
            lines.append('추가 설명: ' + card['note'])
    return '\n'.join(lines)
