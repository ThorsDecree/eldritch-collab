"""Resident-owned motion recipes layered over the existing private image queue."""
from __future__ import annotations

import json
from typing import Any

from .image_rendering import RenderOptions
from .images import ImageService
from .utils import new_id, utc_now_iso


SCHEMA = """
CREATE TABLE IF NOT EXISTS gif_recipes (
    id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL,
    parent_id TEXT,
    brief_json TEXT NOT NULL,
    render_json TEXT NOT NULL,
    prompt TEXT NOT NULL,
    change_note TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gif_recipes_resident ON gif_recipes(resident_id, created_at);
"""


def _text(value: Any, label: str, *, required: bool = False, maximum: int = 4000) -> str:
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f'{label} requires text of at most {maximum} characters')
    return value.strip()


class GifForge:
    def __init__(self, images: ImageService) -> None:
        self.images = images
        self.db = images.db
        self.resident_id = images.resident_id
        with self.db.connect() as connection:
            connection.executescript(SCHEMA)

    def _brief(self, raw: dict[str, Any], render: RenderOptions) -> dict[str, Any]:
        fields = {'subject', 'scene', 'motion', 'invariants', 'camera', 'loop', 'beats', 'reference_ids'}
        if not isinstance(raw, dict) or set(raw) - fields:
            raise ValueError('unknown motion brief fields')
        brief = {name: _text(raw.get(name, default), name, required=name in {'subject', 'motion'})
                 for name, default in [('subject', ''), ('scene', ''), ('motion', ''),
                                       ('camera', 'locked camera'), ('loop', 'Return smoothly to the starting pose')]}
        for name in ('invariants', 'reference_ids'):
            values = raw.get(name, [])
            if not isinstance(values, list) or len(values) > 16:
                raise ValueError(f'{name} must be a list of at most 16 entries')
            brief[name] = [_text(value, name, required=True, maximum=500) for value in values]
        for image_id in brief['reference_ids']:
            path = self.images.resolve_path(image_id)
            if self.images.media(path.read_bytes())['animated']:
                raise ValueError('Forge reference images must be still images')
        beats = raw.get('beats', [])
        if not isinstance(beats, list) or len(beats) > 16:
            raise ValueError('beats must be a list of at most 16 entries')
        normalized = []
        for beat in beats:
            if not isinstance(beat, dict) or set(beat) != {'description', 'frames'}:
                raise ValueError('each beat requires description and frames')
            frames = beat['frames']
            if type(frames) is not int or not 1 <= frames <= 16:
                raise ValueError('beat frames must be integers between 1 and 16')
            normalized.append({'description': _text(beat['description'], 'beat', required=True, maximum=1000),
                               'frames': frames})
        if normalized and sum(beat['frames'] for beat in normalized) != render.frame_count:
            raise ValueError('beat frame budget must equal frame_count')
        brief['beats'] = normalized
        if len(json.dumps(brief)) > 24000:
            raise ValueError('motion brief exceeds 24000 characters')
        return brief

    @staticmethod
    def _compile(brief: dict[str, Any], render: RenderOptions) -> str:
        lines = ['Create one coherent animated GIF.', f'Subject: {brief["subject"]}',
                 f'Scene: {brief["scene"]}', f'Motion: {brief["motion"]}',
                 f'Camera: {brief["camera"]}', f'Loop intention: {brief["loop"]}',
                 f'Frame budget: {render.frame_count}; intended delay: {render.frame_duration_ms} ms.',
                 'Keep fixed: ' + '; '.join(brief['invariants'])]
        start = 1
        for beat in brief['beats']:
            end = start + beat['frames'] - 1
            lines.append(f'Frames {start}-{end}: {beat["description"]}')
            start = end + 1
        lines.append('Keep the scene readable and consistent across frames. These beats guide composition; do not draw labels or a storyboard grid.')
        return '\n'.join(lines)

    def plan(self, brief: dict[str, Any], render: dict[str, Any] | None = None,
             *, parent_id: str | None = None, change_note: str = '') -> dict[str, Any]:
        if render and render.get('output_format', 'gif') != 'gif':
            raise ValueError('Forge recipes require GIF output')
        if parent_id:
            self.get(parent_id)
        return self._save(brief, self.images.render_options({**(render or {}), 'output_format': 'gif'}),
                          parent_id, change_note)

    def _save(self, brief: dict[str, Any], options: RenderOptions,
              parent_id: str | None, change_note: str) -> dict[str, Any]:
        clean = self._brief(brief, options)
        note = _text(change_note, 'change_note')
        recipe_id = new_id('gif')
        with self.db.connect() as connection:
            connection.execute('INSERT INTO gif_recipes VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (recipe_id, self.resident_id, parent_id, json.dumps(clean, sort_keys=True),
                 json.dumps(options.to_dict(), sort_keys=True), self._compile(clean, options), note, utc_now_iso()))
        return self.get(recipe_id)

    def get(self, recipe_id: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            row = connection.execute('SELECT * FROM gif_recipes WHERE id=? AND resident_id=?',
                                     (recipe_id, self.resident_id)).fetchone()
            if row is None:
                raise KeyError('unknown GIF recipe')
            jobs = connection.execute("""SELECT id AS job_id, status, result_json, error_type
                FROM image_jobs WHERE resident_id=? AND json_extract(payload_json, '$.forge_recipe_id')=?
                ORDER BY rowid DESC LIMIT 20""", (self.resident_id, recipe_id)).fetchall()
        result = dict(row)
        result['brief'] = json.loads(result.pop('brief_json'))
        result['render'] = json.loads(result.pop('render_json'))
        result['renders'] = [{**dict(job), 'result': json.loads(job['result_json'])} for job in jobs]
        for job in result['renders']:
            job.pop('result_json')
        result['privacy'] = 'private'
        result['guidance'] = 'Beat timing is prompt guidance, not frame-exact editing; inspect the measured result.'
        return result

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            rows = connection.execute('SELECT id, parent_id, change_note, created_at FROM gif_recipes '
                'WHERE resident_id=? ORDER BY rowid DESC LIMIT ?', (self.resident_id, min(100, max(1, limit)))).fetchall()
        return [dict(row) for row in rows]

    def revise(self, recipe_id: str, changes: dict[str, Any], *, change_note: str) -> dict[str, Any]:
        parent = self.get(recipe_id)
        if not isinstance(changes, dict) or not changes or set(changes) - {'brief', 'render'}:
            raise ValueError('revision changes may contain brief and render only')
        if any(not isinstance(value, dict) for value in changes.values()):
            raise ValueError('brief and render changes must be objects')
        values = {**parent['render'], **changes.get('render', {})}
        if 'model' in changes.get('render', {}) or values.get('output_format') != 'gif':
            raise ValueError('Forge revisions must remain GIFs on the operator-selected model')
        options = RenderOptions.resolve(self.images.config, values, frozen=True)
        return self._save({**parent['brief'], **changes.get('brief', {})}, options, recipe_id,
                          _text(change_note, 'change_note', required=True))

    def render(self, recipe_id: str, *, turn_id: str | None, delivery: dict[str, Any] | None,
               count: int = 1) -> dict[str, Any]:
        recipe = self.get(recipe_id)
        spec = RenderOptions.resolve(self.images.config, recipe['render'], count=count, frozen=True)
        references = recipe['brief']['reference_ids']
        return self.images.queue_job('edit' if references else 'generate',
            {'prompt': recipe['prompt'], 'count': count, 'image_ids': references},
            turn_id=turn_id, delivery=delivery, render=spec, recipe_id=recipe_id)

    def inspect(self, image_id: str, *, maximum: int = 12) -> dict[str, Any]:
        return self.images.inspect_animation(image_id, maximum=maximum)
