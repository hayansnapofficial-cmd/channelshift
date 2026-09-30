"""Server-owned production guidance. Published versions are append-only.

Projects pin the descriptor, never user-supplied instruction text. Change these
instructions by adding a new version, retaining old versions for saved projects.
"""
from __future__ import annotations

import hashlib
import json
from types import MappingProxyType

FORMAT = 'channelshift.production-guides/v1'
CURRENT_VERSION = 'v1'
_V1 = (
    ('design', 'Design from the confirmed scope and real content. Establish a consistent visual '
     'hierarchy, spacing, typography and color system; adapt it to the brand. Specify responsive '
     'mobile and desktop layouts, semantic landmarks, labeled controls, keyboard focus and readable '
     'contrast. Show loading, empty, error and success states. Keep navigation and primary actions '
     'clear. Reference sites inform comparison only: do not copy their code, visual assets or claims. '
     'Do not invent customer content or silently add features.'),
    ('implementation', 'Implement only confirmed requirements and resolved operator choices. Keep '
     'screens, API operations, data entities and access rules traceable to those requirements. '
     'Use server-side authorization and bounded validation, parameterized SQL and safe text rendering. '
     'Do not place secrets, real user records, external credentials or production defaults in output. '
     'Preserve excludes; unsure and later are unresolved decisions, never enabled features. Surface '
     'conflicts with source text or notes as blocking questions rather than choosing silently. '
     'Maintain the permitted runtime and dependency limits; do not execute, deploy or install output.'),
    ('documents_qa', 'Document setup, exact manual launch, configuration placeholders, data location, '
     'access assumptions and unresolved limitations. Define acceptance checks for each confirmed '
     'flow, including permission failures and invalid input. Distinguish checks actually performed '
     'from proposed checks; never claim application execution, deployment, security certification or '
     'customer approval. Retain evidence references, current requirements and review boundaries. '
     'Include factual business disclosures and operator-provided policies; missing facts and legal '
     'sufficiency remain for human review. Use consistent accessible Korean UI for Korean requests.'),
)
_VERSIONS = MappingProxyType({'v1': _V1})
_STAGES = frozenset({'requirements', 'features', 'reference', 'wireframe', 'erd', 'api',
                     'database', 'backend', 'frontend', 'delivery', 'artifacts'})


def descriptor(version=CURRENT_VERSION):
    if type(version) is not str or version not in _VERSIONS:
        raise ValueError('invalid_production_guides')
    sections = _VERSIONS[version]
    encoded = json.dumps({'version': version, 'sections': sections}, ensure_ascii=False,
                         separators=(',', ':')).encode('utf-8')
    return {'format': FORMAT, 'version': version, 'digest': hashlib.sha256(encoded).hexdigest(),
            'sections': [name for name, _ in sections]}


def validate_descriptor(value):
    if type(value) is not dict or value != descriptor(value.get('version')):
        raise ValueError('invalid_production_guides')
    return descriptor(value['version'])


def instructions(stage, version=CURRENT_VERSION):
    if type(stage) is not str or stage not in _STAGES:
        raise ValueError('invalid_production_guides')
    descriptor(version)
    selected = ('design', 'documents_qa') if stage in {'wireframe', 'reference'} else (
        ('implementation', 'documents_qa') if stage in {'erd', 'api', 'database', 'backend'}
        else ('design', 'implementation', 'documents_qa'))
    return '\n'.join(['Server production guides ' + version + ':'] +
                     [name + ': ' + text for name, text in _VERSIONS[version] if name in selected])
