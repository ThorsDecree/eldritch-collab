"""Private resident-authored questions and opt-in, explainable connections."""
from __future__ import annotations

import json
from typing import Any

from .capabilities import CapabilitySpec, object_schema
from .utils import new_id, utc_now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS curiosity_items (
 id TEXT PRIMARY KEY, resident_id TEXT NOT NULL, question TEXT NOT NULL,
 tags_json TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS curiosity_resident ON curiosity_items(resident_id, status, updated_at);
CREATE TABLE IF NOT EXISTS curiosity_settings (
 resident_id TEXT PRIMARY KEY, passive_discovery INTEGER NOT NULL DEFAULT 0
);
"""


def settings(house: Any) -> bool:
    with house.db.connect() as c:
        row = c.execute('SELECT passive_discovery FROM curiosity_settings WHERE resident_id=?', (house.resident_id,)).fetchone()
    return bool(row['passive_discovery']) if row else False


def _item(row: Any) -> dict[str, Any]:
    value = dict(row)
    value['tags'] = json.loads(value.pop('tags_json'))
    value.pop('resident_id', None)
    return value


def _get(house: Any, ident: str) -> dict[str, Any]:
    with house.db.connect() as c:
        row = c.execute('SELECT * FROM curiosity_items WHERE id=? AND resident_id=?', (ident, house.resident_id)).fetchone()
    if row is None:
        raise KeyError('unknown curiosity')
    return _item(row)


def dispatch(house: Any, payload: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    action = payload['action']
    if action == 'curiosity.settings':
        if 'passive_discovery' in payload:
            enabled = payload['passive_discovery']
            with house.db.connect() as c:
                c.execute('INSERT INTO curiosity_settings VALUES (?, ?) ON CONFLICT(resident_id) DO UPDATE SET passive_discovery=excluded.passive_discovery', (house.resident_id, int(enabled)))
        return {'passive_discovery': settings(house), 'privacy': 'resident_private'}
    if action == 'curiosity.add':
        question = payload['question'].strip()
        if not question:
            raise ValueError('question cannot be blank')
        tags = sorted({tag.strip().casefold() for tag in payload.get('tags', []) if tag.strip()})
        ident, now = new_id('curiosity'), utc_now_iso()
        with house.db.connect() as c:
            c.execute('INSERT INTO curiosity_items VALUES (?, ?, ?, ?, ?, ?, ?)', (ident, house.resident_id, question, json.dumps(tags), 'open', now, now))
        return {'item': _get(house, ident), 'privacy': 'resident_private'}
    if action == 'curiosity.list':
        with house.db.connect() as c:
            rows = c.execute('SELECT * FROM curiosity_items WHERE resident_id=? ORDER BY updated_at DESC, id LIMIT ?', (house.resident_id, payload.get('limit', 30))).fetchall()
        return {'items': [_item(r) for r in rows], 'privacy': 'resident_private'}
    item = _get(house, payload['curiosity_id'])
    if action == 'curiosity.show':
        return {'item': item, 'privacy': 'resident_private'}
    if action == 'curiosity.update':
        if not any(key in payload for key in ('question', 'tags', 'status')):
            raise ValueError('provide a change')
        question = payload.get('question', item['question']).strip()
        if not question:
            raise ValueError('question cannot be blank')
        tags = sorted({tag.strip().casefold() for tag in payload.get('tags', item['tags']) if tag.strip()})
        with house.db.connect() as c:
            c.execute('UPDATE curiosity_items SET question=?, tags_json=?, status=?, updated_at=? WHERE id=? AND resident_id=?', (question, json.dumps(tags), payload.get('status', item['status']), utc_now_iso(), item['id'], house.resident_id))
        return {'item': _get(house, item['id']), 'privacy': 'resident_private'}
    if action == 'serendipity.explore':
        from .serendipity_explore import explore
        return explore(house, payload, context or {})
    if action == 'serendipity.suggest':
        if item['status'] == 'closed':
            return {'suggestions': [], 'privacy': 'resident_private'}
        with house.db.connect() as c:
            rows = c.execute("SELECT * FROM curiosity_items WHERE resident_id=? AND id!=? AND status!='closed' ORDER BY updated_at DESC, id LIMIT 501", (house.resident_id, item['id'])).fetchall()
        matches = []
        for row in rows[:500]:
            other = _item(row)
            shared = sorted(set(item['tags']) & set(other['tags']))
            if shared:
                matches.append({'item': other, 'shared_tags': shared, 'reason': 'Shared resident-supplied tags'})
        matches.sort(key=lambda x: (-len(x['shared_tags']), x['item']['id']))
        return {'suggestions': matches[:payload.get('limit', 5)], 'privacy': 'resident_private', 'method': 'shared_tags', 'candidates_scanned': min(len(rows), 500), 'candidate_limit_reached': len(rows) > 500}
    raise ValueError('unknown curiosity action')


def _register(house: Any) -> None:
    with house.db.connect() as c:
        c.executescript(SCHEMA)
    s = lambda **kw: {'type': 'string', **kw}
    ident = s(minLength=3, maxLength=200)
    actions = {
        'curiosity.add': ({'question': s(minLength=1, maxLength=4000), 'tags': {'type': 'array', 'items': s(minLength=1, maxLength=80), 'maxItems': 16}}, ('question',), True),
        'curiosity.list': ({'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}, (), False),
        'curiosity.show': ({'curiosity_id': ident}, ('curiosity_id',), False),
        'curiosity.update': ({'curiosity_id': ident, 'status': s(enum=['open', 'simmering', 'closed']), 'question': s(minLength=1, maxLength=4000), 'tags': {'type': 'array', 'items': s(minLength=1, maxLength=80), 'maxItems': 16}}, ('curiosity_id',), True),
        'curiosity.settings': ({'passive_discovery': {'type': 'boolean'}}, (), True),
        'serendipity.explore': ({'curiosity_id': ident, 'scope': s(minLength=1, maxLength=80), 'terms': {'type': 'array', 'items': s(minLength=2, maxLength=40), 'minItems': 1, 'maxItems': 5}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20}}, ('curiosity_id', 'scope', 'terms'), False),
        'serendipity.suggest': ({'curiosity_id': ident, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20}}, ('curiosity_id',), False),
    }
    examples = {
        'curiosity.add': {'question': 'How do lanterns glow?', 'tags': ['light']},
        'curiosity.list': {}, 'curiosity.show': {'curiosity_id': 'curiosity_example'},
        'curiosity.update': {'curiosity_id': 'curiosity_example', 'status': 'simmering'},
        'curiosity.settings': {'passive_discovery': True},
        'serendipity.suggest': {'curiosity_id': 'curiosity_example'},
        'serendipity.explore': {'curiosity_id': 'curiosity_example', 'scope': 'workspace', 'terms': ['lantern']},
    }
    for name, (fields, required, writes) in actions.items():
        schema = object_schema({'action': {'type': 'string', 'const': name}, **fields, 'after': s(enum=['continue', 'finish'])}, required=('action', *required))
        house.registry.register(CapabilitySpec(name=name, description={
            'curiosity.add': 'Save a private question and optional tags.',
            'curiosity.list': 'Browse your private curiosity shelf.',
            'curiosity.show': 'Inspect one saved question.',
            'curiosity.update': 'Set a question open, simmering, or closed.',
            'curiosity.settings': 'Inspect or toggle optional curation-time discovery.',
            'serendipity.suggest': 'Find related open questions using shared tags.',
            'serendipity.explore': 'Search a chosen local shelf for passages using explicit terms.',
        }[name], effects=('database:write_low_authority',) if writes else ('database:read',),
            group='curiosity', result_visibility='resident_private', input_schema=schema, config_key='house.enabled',
            example_envelopes=({'action': name, **examples[name], 'after': 'continue'},)),
            lambda payload, context: dispatch(house, payload, context))


def register_composition() -> None:
    from .composition import register_capability_installer, register_workbench_provider
    register_capability_installer('curiosity.shelf', _register, order=85)
    register_workbench_provider('curiosity.shelf', workbench_provider, order=40)


def curation_invitation(house: Any) -> str:
    if not house.registry.spec('curiosity.add').enabled(house.config) or not settings(house):
        return ''
    return ('\nOptional curiosity discovery is enabled. If a question in this curation '
            'material catches your interest, you may save it using curiosity.add with tags. '
            'You may save nothing. Do not treat others\' questions as your commitments. '
            'Saved questions are private; do not infer permission to share.')


def workbench_provider(house: Any, request: dict[str, Any]) -> dict[str, Any]:
    from .utils import sha256_text, stable_json
    if request.get('mode', 'view') == 'view':
        cards = []
        if house.registry.spec('curiosity.show').enabled(house.config) and request.get('lane') in ('all', 'tend') and request.get('limit', 0) > 0:
            with house.db.connect() as c:
                rows = c.execute("SELECT * FROM curiosity_items WHERE resident_id=? AND status!='closed' ORDER BY updated_at DESC, id LIMIT ?", (house.resident_id, request['limit'])).fetchall()
            for row in rows:
                item = _item(row)
                fingerprint = sha256_text(stable_json({'resident': house.resident_id, **item}))
                cards.append({'card_id': 'wb_' + fingerprint[:24], 'state_fingerprint': fingerprint,
                    'provider': 'curiosity.shelf', 'lane': 'tend', 'kind': 'curiosity',
                    'title': item['question'], 'summary': 'A question on your curiosity shelf.',
                    'why_now': 'saved curiosity', 'last_touched': item['updated_at'],
                    'effect_class': 'read_only', 'source': {'curiosity_id': item['id']},
                    'actions': [{'action_id': 'open', 'label': 'Open', 'effect_class': 'read_only',
                                 'effects': ['database:read'], 'cost_class': 'free', 'confirmation': 'none', 'outward_facing': False}]})
        return {'implemented_lanes': ['tend'], 'cards': cards}
    card = request['card']
    if request.get('action_id') != 'open':
        raise ValueError('unknown curiosity card action')
    context = dict(request.get('context') or {})
    context['source_envelope'] = 'WORKBENCH'
    result = house.dispatch({'action': 'curiosity.show', 'curiosity_id': card['source']['curiosity_id']}, turn_id=context.get('turn_id'), context=context)
    return {'underlying_action': 'curiosity.show', 'underlying_receipt_id': result.get('receipt_id'),
            'outcome': result, 'underlying_result': result, 'refreshed_card': card,
            'outward_effect': 'none', 'memory_promotion': False, 'identity_effect': False}
