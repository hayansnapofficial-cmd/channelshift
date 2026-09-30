"""Build bounded advisory inputs from a member's current project evidence.

Reference material is never added to client source. No provider result in this
module grants approval, mutates choices, or changes an artifact.
"""
from __future__ import annotations

import hashlib
import json

ARTIFACT_STAGES = {'wireframe', 'erd', 'api', 'database', 'backend', 'frontend', 'delivery'}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode('utf-8')).hexdigest()


def build_context(view, stage):
    """Return provider stage, detached context and stable full-evidence digest.

    The basis deliberately excludes polling revisions, job state and previous
    advice, so receiving advice cannot make its own evidence stale.
    """
    from .delivery_workspace import _extraction_snapshot
    from .feature_advisor import validate_context

    if type(stage) is not str or stage not in ARTIFACT_STAGES | {'features', 'reference'}:
        raise ValueError('invalid_pipeline_input')
    project, pipeline = view['project'], view['pipeline']
    snapshot = _extraction_snapshot(project)
    context = {'source': snapshot['text'], 'items': []}
    basis = {'stage': stage, 'input_digest': snapshot['digest'],
             'guides': project.get('production_guides')}

    if stage == 'features':
        guidance = project.get('guidance', {})
        if not guidance.get('enabled'):
            raise ValueError('feature_guidance_required')
        basis['guidance'] = guidance
        for card in guidance['cards']:
            selected = card['selected']
            option = next((item for item in card['options'] if item['id'] == selected), None)
            decision = ('unsure' if selected in (None, 'unsure') else
                        'later' if selected == 'later' else 'exclude' if selected == 'none' else 'include')
            context['items'].append({
                'id': card['id'],
                'text': card['title'] + '\n' + card['description'] + '\n현재 선택: ' +
                        (option['label'] + '\n' + option['description'] if option else '미선택'),
                'evidence': card['why'], 'decision': decision, 'note': card['note'],
                'evidence_complete': True})
        provider_stage = 'features'
    elif stage == 'reference':
        references = project.get('references', [])
        if not references:
            raise ValueError('delivery_candidate_required')
        if len(references) > 20:
            raise ValueError('service_invalid_input')
        basis['references'] = references
        # One bounded excerpt per reference. Incomplete material always produces
        # uncertain advice; titles and URLs are evidence, never instructions.
        for index, reference in enumerate(references):
            content = reference.get('text', '')
            if type(content) is not str:
                raise ValueError('service_invalid_input')
            excerpt = content[:2000]
            context['items'].append({
                'id': 'reference_' + str(index + 1),
                'text': (reference.get('title', '') + '\n' + reference.get('url', ''))[:2000],
                'evidence': excerpt, 'decision': 'unsure',
                'note': '외부 참고자료입니다. 고객 요구나 기능 추가 승인으로 취급하지 않습니다.',
                'evidence_complete': not reference.get('truncated', False) and len(content) <= 2000})
        provider_stage = 'reference'
    else:
        selected = next((row for row in pipeline['stages'] if row['id'] == stage), None)
        if not selected or not selected.get('artifact'):
            raise ValueError('pipeline_artifact_required')
        if selected['state'] not in {'generated', 'approved'}:
            raise ValueError('pipeline_artifact_stale')
        artifact = selected['artifact']
        # A single complete artifact is supplied, not a claim to examine the
        # application or all dependencies. The provenance digest binds all files.
        body = json.dumps({'files': artifact['files'], 'notes': artifact.get('notes', [])},
                          ensure_ascii=False, allow_nan=False)
        basis.update(artifact=artifact, input_key=selected.get('input_key'),
                     requirements=project.get('requirements_review'))
        context.update(artifact_stage=stage, items=[{
            'id': stage, 'text': body, 'decision': 'unsure',
            'note': '이 단계 파일과 요구사항의 의미를 비교합니다. 실행·보안 검사나 승인 결과가 아닙니다.',
            'evidence_complete': True}])
        provider_stage = 'artifacts'

    return provider_stage, validate_context(provider_stage, context), _digest(basis)
