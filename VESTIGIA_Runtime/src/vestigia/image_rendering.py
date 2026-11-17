"""Render contracts and bounded, byte-derived media inspection (no network)."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from io import BytesIO
import json
import re
from typing import Any

from PIL import Image, ImageDraw, PngImagePlugin

from .config import ResolvedConfig


RENDER_FIELDS = ('output_format', 'frame_count', 'frame_duration_ms', 'size', 'quality', 'complexity')
GIF_MODELS = frozenset({'gpt-image-2.6-sunburst', 'gpt-image-2.6-flare'})
FORMATS = {'PNG': ('png', 'image/png', '.png'), 'JPEG': ('jpeg', 'image/jpeg', '.jpg'),
           'WEBP': ('webp', 'image/webp', '.webp'), 'GIF': ('gif', 'image/gif', '.gif')}


def _integer(value: Any, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer between {low} and {high}')
    return value


@dataclass(frozen=True)
class RenderOptions:
    model: str
    output_format: str = 'png'
    size: str = 'auto'
    quality: str = 'auto'
    complexity: str | None = None
    frame_count: int | None = None
    frame_duration_ms: int | None = None

    @classmethod
    def resolve(cls, config: ResolvedConfig, values: dict[str, Any] | None = None,
                *, count: int = 1, frozen: bool = False) -> RenderOptions:
        values = dict(values or {})
        allowed = set(RENDER_FIELDS) | ({'model'} if frozen else set())
        if set(values) - allowed:
            raise ValueError('unknown render fields: ' + ', '.join(sorted(set(values) - allowed)))
        output_format = values.get('output_format', 'png')
        if output_format not in {'png', 'jpeg', 'webp', 'gif'}:
            raise ValueError('output_format must be png, jpeg, webp, or gif')
        gif = output_format == 'gif'
        model = values.get('model') if frozen else None
        if not model:
            model = config.get('images.gif_model', 'gpt-image-2.6-flare') if gif else config.get('models.image')
        if gif and model not in GIF_MODELS:
            raise ValueError('configured GIF model must be gpt-image-2.6-sunburst or gpt-image-2.6-flare')
        quality = values.get('quality', config.get('images.default_quality', 'auto'))
        if quality not in {'auto', 'low', 'medium', 'high', 'standard', 'hd'}:
            raise ValueError('unsupported rendering quality')
        if gif and quality not in {'auto', 'low', 'medium', 'high'}:
            raise ValueError('GIF quality must be auto, low, medium, or high')
        complexity = values.get('complexity')
        if complexity is not None:
            if complexity not in {'low', 'medium', 'high'} or model not in GIF_MODELS:
                raise ValueError('complexity requires a supported 2.6 model and low, medium, or high')
        size = values.get('size', config.get('images.default_size', 'auto'))
        if not isinstance(size, str) or (size != 'auto' and not re.fullmatch(r'[1-9][0-9]{0,4}x[1-9][0-9]{0,4}', size)):
            raise ValueError('size must be auto or WIDTHxHEIGHT')
        frames, delay = values.get('frame_count'), values.get('frame_duration_ms')
        if not gif and (frames is not None or delay is not None):
            raise ValueError('frame controls require output_format=gif')
        _integer(count, 'count', 1, int(config.get('images.max_per_request', 2)))
        if gif:
            frames = _integer(9 if frames is None else frames, 'frame_count', 2, 16)
            delay = _integer(200 if delay is None else delay, 'frame_duration_ms', 20, 2000)
            delay = ((delay + 5) // 10) * 10
            if size == 'auto':
                pixels = int(config.get('images.gif_auto_frame_pixels', 1048576))
            else:
                width, height = map(int, size.split('x'))
                if width % 16 or height % 16:
                    raise ValueError('GIF dimensions must be multiples of 16')
                pixels = width * height
            if pixels * frames * count > int(config.get('images.max_gif_request_pixels', 16777216)):
                raise ValueError('GIF request exceeds configured total pixel budget; reduce size, frames, or count')
        return cls(str(model), output_format, size, quality, complexity, frames, delay)

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class RenderedImage:
    data: bytes
    model: str
    usage: dict[str, Any] | None = None


def inspect_media(data: bytes, *, max_bytes: int = 20000000, max_frames: int = 256,
                  max_pixels: int = 67108864) -> dict[str, Any]:
    if not data or len(data) > max_bytes:
        raise ValueError('empty image or encoded byte ceiling exceeded')
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in FORMATS:
                raise ValueError('unsupported image format')
            fmt, mime, suffix = FORMATS[image.format]
            width, height = image.size
            if width * height > max_pixels:
                raise ValueError('decoded pixel ceiling exceeded')
            loop = image.info.get('loop')
            durations: list[int | None] = []
            sizes: list[list[int]] = []
            decoded_pixels = 0
            for index in range(max_frames + 1):
                try:
                    image.seek(index)
                except EOFError:
                    break
                frame_width, frame_height = image.size
                decoded_pixels += frame_width * frame_height
                if index >= max_frames or decoded_pixels > max_pixels:
                    raise ValueError('decoded frame/pixel ceiling exceeded')
                image.load()  # validate every frame, not only its header
                sizes.append([frame_width, frame_height])
                width, height = max(width, frame_width), max(height, frame_height)
                duration = image.info.get('duration')
                durations.append(int(duration) if duration is not None else None)
            return {'format': fmt, 'mime_type': mime, 'suffix': suffix,
                    'width': width, 'height': height, 'byte_size': len(data),
                    'frame_sizes': sizes, 'decoded_pixels': decoded_pixels,
                    'animated': len(durations) > 1, 'frame_count': len(durations),
                    'frame_durations_ms': durations,
                    'duration_ms': sum(durations) if all(d is not None for d in durations) else None,
                    'loop': loop}
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError('attachment bytes are not a valid image (malformed or excessive data)') from exc


def contact_sheet(data: bytes, *, maximum: int = 12,
                  media: dict[str, Any] | None = None,
                  provenance: dict[str, Any] | None = None) -> tuple[bytes, dict[str, Any]]:
    maximum = _integer(maximum, 'maximum', 2, 16)
    media = media or inspect_media(data)
    total = media['frame_count']
    selected = min(total, maximum)
    indices = [0] if selected == 1 else [round(i * (total - 1) / (selected - 1)) for i in range(selected)]
    timestamps, elapsed = [], 0
    for delay in media['frame_durations_ms']:
        timestamps.append(elapsed)
        elapsed = elapsed + delay if elapsed is not None and delay is not None else None
    columns = min(4, selected)
    sheet = Image.new('RGB', (256 * columns, 220 * ((selected + columns - 1) // columns)), '#202027')
    draw = ImageDraw.Draw(sheet)
    with Image.open(BytesIO(data)) as image:
        for cell, index in enumerate(indices):
            image.seek(index)
            frame = image.convert('RGBA')
            frame.thumbnail((248, 192))
            x, y = (cell % columns) * 256 + 4, (cell // columns) * 220 + 4
            sheet.paste(frame, (x, y), frame)
            stamp = timestamps[index]
            draw.text((x, y + 194), f'Frame {index} | {stamp if stamp is not None else "?"} ms', fill='white')
    output = BytesIO()
    coverage = {'recipe': 'gif-contact-sheet-v1', 'frame_indices': indices,
                              'timestamps_ms': [timestamps[i] for i in indices],
                              'total_frames': total, 'exhaustive': selected == total,
                              'interpretation': 'sampled still frames; motion between samples is not observed'}
    png_info = PngImagePlugin.PngInfo()
    if provenance is not None:
        # Derivation identity travels in the bytes. Equal pixels from distinct
        # parents must not collapse their provenance or inherit sharing state.
        png_info.add_text('vestigia.derivation', json.dumps(
            {'parent': provenance, 'coverage': coverage}, sort_keys=True))
    sheet.save(output, format='PNG', pnginfo=png_info)
    return output.getvalue(), coverage
