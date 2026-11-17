from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.home import initialize_home


def animation(*, durations=(100, 230, 400), loop=0):
    frames = [Image.new('RGB', (32, 32), color) for color in ('red', 'green', 'blue')]
    out = BytesIO()
    extra = {} if loop is None else {'loop': loop}
    frames[0].save(out, format='GIF', save_all=True, append_images=frames[1:],
                   duration=list(durations), disposal=2, **extra)
    return out.getvalue()


@pytest.fixture
def home(tmp_path):
    path = initialize_home(tmp_path / 'home', name='Forge Resident', glyph='F')
    config = load_config(path)
    db = ContinuityDB(path / 'memory' / 'continuity.db')
    return config, db


def test_render_options_route_gifs_and_round_timing(home):
    from vestigia.image_rendering import RenderOptions
    config, _ = home
    spec = RenderOptions.resolve(config, {'output_format': 'gif', 'frame_duration_ms': 235})
    assert spec.model == 'gpt-image-2.6-flare'
    assert spec.frame_count == 9
    assert spec.frame_duration_ms == 240
    assert spec.to_dict()['output_format'] == 'gif'
    still = RenderOptions.resolve(config, {})
    assert still.model == config.get('models.image')
    assert still.frame_count is None
    assert still.quality == 'auto'


@pytest.mark.parametrize('values', [
    {'frame_count': 3}, {'output_format': 'gif', 'frame_count': 1},
    {'output_format': 'gif', 'frame_count': 17},
    {'output_format': 'gif', 'frame_count': True},
    {'output_format': 'gif', 'frame_duration_ms': 19},
    {'output_format': 'gif', 'frame_duration_ms': 2001},
    {'output_format': 'gif', 'size': '513x512'},
    {'quality': 'super'}, {'complexity': 'auto'}, {'output_format': 'svg'},
    {'output_format': 'gif', 'size': '4096x4096'},
])
def test_render_options_reject_invalid_combinations_before_render(home, values):
    from vestigia.image_rendering import RenderOptions
    with pytest.raises(ValueError):
        RenderOptions.resolve(home[0], values)


def test_animation_workload_counts_all_outputs(home):
    from vestigia.image_rendering import RenderOptions
    with pytest.raises(ValueError, match='pixel'):
        RenderOptions.resolve(home[0], {'output_format': 'gif', 'size': '1024x1024',
                                      'frame_count': 16}, count=2)


def test_media_measures_nonuniform_delays_and_absent_loop():
    from vestigia.image_rendering import inspect_media
    media = inspect_media(animation(loop=None))
    assert media['format'] == 'gif'
    assert media['mime_type'] == 'image/gif'
    assert media['frame_count'] == 3
    assert media['frame_durations_ms'] == [100, 230, 400]
    assert media['duration_ms'] == 730
    assert media['loop'] is None
    assert media['animated'] is True


def test_media_decode_limits_are_enforced():
    from vestigia.image_rendering import inspect_media
    for kwargs in ({'max_frames': 2}, {'max_pixels': 2000}, {'max_bytes': 10}):
        with pytest.raises(ValueError):
            inspect_media(animation(), **kwargs)
    with pytest.raises(ValueError):
        inspect_media(b'GIF89a broken')


def test_contact_sheet_reports_sampled_frames_and_timestamps():
    from vestigia.image_rendering import contact_sheet
    png, coverage = contact_sheet(animation(), maximum=2)
    assert coverage['frame_indices'] == [0, 2]
    assert coverage['timestamps_ms'] == [0, 330]
    assert coverage['exhaustive'] is False
    assert coverage['total_frames'] == 3
    assert coverage['recipe'] == 'gif-contact-sheet-v1'
    with Image.open(BytesIO(png)) as sheet:
        assert sheet.format == 'PNG'
        assert sheet.width <= 1024


class FixtureProvider:
    name = 'fixture'
    model = 'fixture-image'

    def __init__(self, blobs=None):
        self.blobs = blobs or [animation()]
        self.calls = []

    def generate(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return self.blobs

    def edit(self, prompt, **kwargs):
        return self.generate(prompt, **kwargs)


def test_native_gif_save_preserves_bytes_metadata_and_dedup(home):
    from vestigia.images import ImageService
    service = ImageService(*home, provider=FixtureProvider())
    result = service.generate('Lantern flicker', render={'output_format': 'gif', 'frame_count': 3})
    assert result.paths[0].suffix == '.gif'
    assert result.paths[0].read_bytes() == animation()
    asset = service.get_asset(result.image_ids[0])
    assert asset['media_type'] == 'image/gif'
    assert asset['media']['frame_durations_ms'] == [100, 230, 400]
    assert asset['source']['render']['frame_count'] == 3
    assert asset['privacy'] == 'private'
    again = service.ingest_bytes(animation(), filename='mislabel.png')
    assert again['id'] == asset['id']
    assert result.media[0]['duration_ms'] == 730
    reloaded = ImageService(*home, fake=True)
    assert reloaded.get_asset(asset['id'])['media']['frame_count'] == 3


def test_ingest_uses_signature_instead_of_untrusted_filename(home):
    from vestigia.images import ImageService
    service = ImageService(*home, fake=True)
    asset = service.ingest_bytes(animation(), filename='claimed.png')
    assert asset['media_type'] == 'image/gif'
    assert service.resolve_path(asset['id']).suffix == '.gif'


def test_rejects_wrong_format_or_static_gif_without_partial_artifacts(home):
    from vestigia.images import FakeImageProvider, ImageService
    one = BytesIO()
    Image.new('RGB', (8, 8), 'red').save(one, format='GIF')
    for invalid in [FakeImageProvider._PNG, one.getvalue(), b'broken']:
        service = ImageService(*home, provider=FixtureProvider([animation(), invalid]))
        with pytest.raises(ValueError):
            service.generate('Loop', count=2, render={'output_format': 'gif', 'frame_count': 3})
        assert service.history() == []


def test_queued_render_settings_survive_restart_and_changed_defaults(home):
    from vestigia.images import ImageService
    config, db = home
    service = ImageService(config, db, fake=True)
    job = service.queue_job('generate', {'prompt': 'Cat', 'output_format': 'gif',
        'frame_count': 3, 'frame_duration_ms': 230, 'quality': 'low', 'size': '512x512',
        'complexity': 'medium'}, turn_id=None, delivery=None)
    config.data['images']['default_size'] = '1024x1024'
    config.data['images']['default_quality'] = 'high'
    config.data['images']['gif_model'] = 'gpt-image-2.6-sunburst'
    provider = FixtureProvider()
    restarted = ImageService(config, db, provider=provider)
    claimed = restarted.claim_next_job()
    assert claimed['id'] == job['job_id']
    result = restarted.execute_job(claimed['id'])
    assert result['status'] == 'completed'
    spec = provider.calls[0][1]['render']
    assert spec.size == '512x512'
    assert spec.model == 'gpt-image-2.6-flare'
    assert spec.quality == 'low'
    assert spec.complexity == 'medium'
    assert result['render']['frame_duration_ms'] == 230
    assert result['media'][0]['duration_ms'] == 730
    assert result['publication'] is False


def test_stale_render_is_failed_as_uncertain_without_auto_requeue(home):
    from vestigia.images import ImageService
    service = ImageService(*home, fake=True)
    job = service.queue_job('generate', {'prompt': 'Cat'}, turn_id=None, delivery=None)
    service.claim_next_job()
    with home[1].connect() as conn:
        conn.execute("UPDATE image_jobs SET updated_at='2000-01-01T00:00:00+00:00' WHERE id=?", (job['job_id'],))
    restarted = ImageService(*home, fake=True)
    assert restarted.claim_next_job() is None
    failed = restarted.unnotified_jobs()[0]
    assert failed['status'] == 'failed'
    assert failed['error_type'] == 'RenderOutcomeUnknown'


def test_provider_encodes_new_fields_at_sdk_boundary_without_network(home):
    import base64
    from types import SimpleNamespace
    from vestigia.image_rendering import RenderOptions
    from vestigia.images import OpenAIImageProvider
    captured = []

    def generate(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(animation()).decode())],
                               usage={'total_tokens': 25})

    provider = object.__new__(OpenAIImageProvider)
    provider.model = 'gpt-image-2'
    provider.client = SimpleNamespace(images=SimpleNamespace(generate=generate))
    render = RenderOptions.resolve(home[0], {'output_format': 'gif', 'frame_count': 3, 'complexity': 'low'})
    results = provider.generate('cat', count=1, size='auto', quality='auto', render=render)
    assert captured[0]['model'] == 'gpt-image-2.6-flare'
    assert captured[0]['extra_body'] == {'output_format': 'gif', 'frame_count': 3,
                                         'frame_duration_ms': 200, 'complexity': 'low'}
    assert results[0].data == animation()
    assert results[0].usage == {'total_tokens': 25}


def test_animation_vision_uses_labelled_frames_and_coverage_cache(home):
    from vestigia.images import ImageService
    seen = []

    class Vision:
        name, model = 'fixture', 'fixture-vision'
        def inspect(self, image, *, question, detail):
            seen.append((image, question))
            assert image.suffix == '.png'
            return 'Sampled frames show a color change.'

    service = ImageService(*home, vision_provider=Vision())
    asset = service.ingest_bytes(animation(), filename='loop.gif')
    result = service.inspect(asset['id'], routes=['vision_low'])
    assert result['coverage']['frame_indices'] == [0, 1, 2]
    assert 'sampled' in seen[0][1].lower()
    assert service.inspect(asset['id'], routes=['vision_low'])['results'][0]['cached'] is True
    assert len(seen) == 1


def brief():
    return {'subject': 'Librarian cat beside a lantern', 'motion': 'Tail curls, then relaxes',
            'invariants': ['Cat face and bookshelf stay fixed'], 'camera': 'locked',
            'loop': 'Return to the starting tail pose',
            'beats': [{'description': 'Curl tail', 'frames': 2},
                      {'description': 'Relax to start', 'frames': 1}]}


def test_forge_recipes_persist_and_branch_without_rendering(home):
    from vestigia.gif_forge import GifForge
    from vestigia.images import ImageService
    provider = FixtureProvider()
    images = ImageService(*home, provider=provider)
    forge = GifForge(images)
    first = forge.plan(brief(), {'frame_count': 3, 'frame_duration_ms': 200})
    revised = forge.revise(first['id'], {'brief': {'motion': 'Lantern gently brightens'}},
                           change_note='Keep the cat still')
    sibling = forge.revise(first['id'], {'render': {'quality': 'low'}}, change_note='Quick study')
    assert revised['parent_id'] == sibling['parent_id'] == first['id']
    assert revised['id'] != sibling['id']
    assert forge.get(first['id'])['brief']['motion'] == 'Tail curls, then relaxes'
    assert forge.get(revised['id'])['brief']['invariants'] == ['Cat face and bookshelf stay fixed']
    assert 'Frame' in revised['prompt']
    assert 'Lantern gently brightens' in revised['prompt']
    assert len(GifForge(ImageService(*home, fake=True)).list()) == 3
    assert provider.calls == []


def test_forge_rejects_inconsistent_beat_budget_and_unknown_changes(home):
    from vestigia.gif_forge import GifForge
    from vestigia.images import ImageService
    forge = GifForge(ImageService(*home, fake=True))
    with pytest.raises(ValueError, match='beat'):
        forge.plan(brief(), {'frame_count': 4})
    recipe = forge.plan(brief(), {'frame_count': 3})
    with pytest.raises(ValueError):
        forge.revise(recipe['id'], {'publication': True}, change_note='bad')
    with pytest.raises(ValueError):
        forge.plan({**brief(), 'mystery': 'ignored?'}, {'frame_count': 3})


def test_forge_queues_explicit_render_with_recipe_lineage(home):
    from vestigia.gif_forge import GifForge
    from vestigia.images import ImageService
    images = ImageService(*home, provider=FixtureProvider())
    forge = GifForge(images)
    recipe = forge.plan(brief(), {'frame_count': 3, 'quality': 'low'})
    job = forge.render(recipe['id'], turn_id=None, delivery=None)
    assert job['status'] == 'queued'
    assert images.provider.calls == []
    claimed = images.claim_next_job()
    result = images.execute_job(claimed['id'])
    assert result['status'] == 'completed'
    assert result['forge_recipe_id'] == recipe['id']
    asset = images.get_asset(result['image_ids'][0])
    assert asset['source']['forge_recipe_id'] == recipe['id']
    review = forge.inspect(asset['id'])
    sheet = images.get_asset(review['contact_sheet_id'])
    assert sheet['source']['parent_image_id'] == asset['id']
    assert sheet['privacy'] == 'private'
    assert forge.get(recipe['id'])['renders'][0]['job_id'] == job['job_id']


def test_forge_rejects_other_resident_recipe_and_animated_reference(home):
    from vestigia.gif_forge import GifForge
    from vestigia.images import ImageService
    import copy
    images = ImageService(*home, fake=True)
    forge = GifForge(images)
    recipe = forge.plan(brief(), {'frame_count': 3})
    other = copy.deepcopy(home[0])
    other.data['resident']['id'] = 'other-resident'
    outsider = GifForge(ImageService(other, home[1], fake=True))
    with pytest.raises(KeyError):
        outsider.get(recipe['id'])
    with pytest.raises(KeyError):
        outsider.render(recipe['id'], turn_id=None, delivery=None)
    assert outsider.list() == []
    image = images.ingest_bytes(animation(), filename='input.gif')
    with pytest.raises(ValueError, match='still'):
        forge.plan({**brief(), 'reference_ids': [image['id']]}, {'frame_count': 3})


def test_formal_dispatch_exposes_rendering_and_forge_and_enforces_confirmation(home):
    from vestigia.house_tools import HousePort
    from vestigia.images import ImageService
    images = ImageService(*home, fake=True)
    house = HousePort(*home, image_service=images)
    result, _, _ = house.registry.dispatch({'action': 'image.generate', 'prompt': 'cat',
        'background': False, 'output_format': 'gif', 'frame_count': 3, 'frame_duration_ms': 200})
    assert result['media'][0]['animated'] is True
    recipe, _, _ = house.registry.dispatch({'action': 'gif.plan', 'brief': brief(), 'render': {'frame_count': 3}})
    assert recipe['render']['output_format'] == 'gif'
    home[0].data['images']['require_confirmation'] = True
    with pytest.raises(PermissionError):
        house.registry.dispatch({'action': 'gif.render', 'recipe_id': recipe['id'], 'confirmed': True})


def test_cli_fake_gif_roundtrip_and_edit_flags(home, capsys):
    import json
    from vestigia.cli import main
    from vestigia.images import FakeImageProvider
    from pathlib import Path
    path = home[0].home_path
    assert main(['image', 'generate', str(path), 'Lantern loop', '--fake',
                 '--output-format', 'gif', '--frame-count', '3', '--frame-duration-ms', '235',
                 '--size', '512x512', '--quality', 'low', '--complexity', 'medium']) == 0
    generated = json.loads(capsys.readouterr().out)
    assert generated['render']['frame_duration_ms'] == 240
    assert generated['media'][0]['frame_count'] == 3
    assert Path(generated['paths'][0]).suffix == '.gif'
    source = path / 'workspace' / 'reference.png'
    source.write_bytes(FakeImageProvider._PNG)
    assert main(['image', 'edit', str(path), 'Lantern sways', '--fake', '--source', str(source),
                 '--output-format', 'gif', '--frame-count', '2']) == 0
    edited = json.loads(capsys.readouterr().out)
    assert edited['media'][0]['frame_count'] == 2


def test_drawer_labels_measured_animation_and_old_unknown_metadata():
    from vestigia.web_app import _animation_label
    assert _animation_label({'media': {'animated': True, 'frame_count': 3, 'duration_ms': 730}}) == '3 frames · 730 ms'
    assert _animation_label({'media': None}) == ''
    assert _animation_label({'media': {'animated': False}}) == ''


def test_legacy_queued_job_without_render_options_still_completes(home):
    import json
    from vestigia.images import ImageService
    images = ImageService(*home, fake=True)
    job = images.queue_job('generate', {'prompt': 'still image'}, turn_id=None, delivery=None)
    with home[1].connect() as conn:
        conn.execute('UPDATE image_jobs SET payload_json=? WHERE id=?',
                     (json.dumps({'prompt': 'legacy still', 'count': 1, 'confirmed': False}), job['job_id']))
    images.claim_next_job()
    result = images.execute_job(job['job_id'])
    assert result['status'] == 'completed'
    assert result['media'][0]['format'] == 'png'


def test_gif_reference_edit_and_disable_gate_use_existing_service(home):
    from vestigia.gif_forge import GifForge
    from vestigia.images import ImageService, FakeImageProvider
    images = ImageService(*home, fake=True)
    source = images.ingest_bytes(FakeImageProvider._PNG, filename='source.png')
    forge = GifForge(images)
    recipe = forge.plan({**brief(), 'reference_ids': [source['id']]}, {'frame_count': 3})
    job = forge.render(recipe['id'], turn_id=None, delivery=None)
    assert job['operation'] == 'edit'
    images.claim_next_job()
    assert images.execute_job(job['job_id'])['status'] == 'completed'
    home[0].data['images']['edits_enabled'] = False
    with pytest.raises(PermissionError):
        forge.render(recipe['id'], turn_id=None, delivery=None)


def test_still_provider_request_preserves_legacy_format_default(home):
    from vestigia.images import OpenAIImageProvider
    from vestigia.image_rendering import RenderOptions
    provider = object.__new__(OpenAIImageProvider)
    provider.model = 'gpt-image-2'
    options = RenderOptions.resolve(home[0], {})
    request = provider._request('cat', 1, 'auto', 'auto', options)
    assert 'extra_body' not in request


def test_forge_capability_examples_validate_and_plan_runs(home):
    from vestigia.capabilities import validate_instance
    from vestigia.capability_contracts import contract_for
    from vestigia.house_tools import HousePort
    from vestigia.images import ImageService
    house = HousePort(*home, image_service=ImageService(*home, fake=True))
    for name in ('gif.plan', 'gif.revise', 'gif.show', 'gif.list', 'gif.render', 'gif.inspect'):
        contract = contract_for(name)
        for example in contract['example_envelopes']:
            validate_instance(example, contract['input_schema'])
    result, _, _ = house.registry.dispatch(contract_for('gif.plan')['example_envelopes'][0])
    assert result['render']['output_format'] == 'gif'


def test_forge_does_not_silently_override_requested_non_gif_format(home):
    from vestigia.gif_forge import GifForge
    from vestigia.images import ImageService
    with pytest.raises(ValueError, match='GIF'):
        GifForge(ImageService(*home, fake=True)).plan(brief(), {'output_format': 'png', 'frame_count': 3})


def test_real_sdk_serializes_gif_generation_and_edit_with_mock_transport(home):
    import base64
    import json
    httpx = pytest.importorskip('httpx')
    from openai import OpenAI
    from vestigia.image_rendering import RenderOptions
    from vestigia.images import OpenAIImageProvider, FakeImageProvider
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'created': 1, 'data': [
            {'b64_json': base64.b64encode(animation()).decode()}],
            'usage': {'total_tokens': 25, 'input_tokens': 5, 'output_tokens': 20}}, request=request)

    provider = object.__new__(OpenAIImageProvider)
    provider.model = 'gpt-image-2'
    spec = RenderOptions.resolve(home[0], {'output_format': 'gif', 'frame_count': 3, 'complexity': 'low'})
    source = home[0].home_path / 'workspace' / 'ref.png'
    source.write_bytes(FakeImageProvider._PNG)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        provider.client = OpenAI(api_key='fixture-not-a-real-key', http_client=client, max_retries=0)
        assert provider.generate('cat', count=1, size='512x512', quality='low', render=spec)[0].data == animation()
        assert provider.edit('cat', source_images=[source], count=1, size='512x512', quality='low', render=spec)[0].data == animation()
    generated = json.loads(requests[0].content)
    assert generated['frame_count'] == 3
    assert generated['output_format'] == 'gif'
    assert generated['complexity'] == 'low'
    multipart = requests[1].content
    assert b'name="frame_count"\r\n\r\n3' in multipart
    assert b'name="output_format"\r\n\r\ngif' in multipart
    assert b'name="complexity"\r\n\r\nlow' in multipart


def expanding_canvas_gif():
    stream = BytesIO()
    red = Image.new('RGB', (16, 16), 'red')
    red.save(stream, format='GIF', save_all=True, append_images=[Image.new('RGB', (16, 16), 'blue')],
             duration=100, optimize=False)
    data = bytearray(stream.getvalue())
    descriptor = b'\x2c\x00\x00\x00\x00\x10\x00\x10\x00'
    first = data.index(descriptor)
    second = data.index(descriptor, first + 1)
    data[second + 1:second + 3] = (100).to_bytes(2, 'little')
    return bytes(data)


def test_decoder_counts_expanded_later_canvas_before_loading():
    from vestigia.image_rendering import inspect_media
    data = expanding_canvas_gif()
    with Image.open(BytesIO(data)) as image:
        image.seek(1)
        assert image.size == (116, 16)
    with pytest.raises(ValueError, match='pixel'):
        inspect_media(data, max_pixels=512)


def test_decoder_reports_actual_frame_dimensions_and_pixel_count():
    from vestigia.image_rendering import inspect_media
    media = inspect_media(expanding_canvas_gif(), max_pixels=4096)
    assert media['width'] == 116
    assert media['frame_sizes'] == [[16, 16], [116, 16]]
    assert media['decoded_pixels'] == 2112


def test_contact_sheet_parent_identity_and_reused_privacy_are_truthful(home):
    from vestigia.images import ImageService
    images = ImageService(*home, fake=True)
    one = images.ingest_bytes(animation(loop=0), filename='forever.gif')
    two = images.ingest_bytes(animation(loop=1), filename='once.gif')
    first = images.inspect_animation(one['id'])
    images.review(first['contact_sheet_id'], 'share', actor='resident', reason='Explicit review')
    second = images.inspect_animation(two['id'])
    assert second['contact_sheet_id'] != first['contact_sheet_id']
    asset = images.get_asset(second['contact_sheet_id'])
    assert asset['source']['parent_image_id'] == two['id']
    assert asset['privacy'] == second['privacy'] == 'private'
    repeated = images.inspect_animation(one['id'])
    assert repeated['contact_sheet_id'] == first['contact_sheet_id']
    assert repeated['privacy'] == 'shareable'


def test_animated_ocr_does_not_reuse_old_first_frame_cache(home, monkeypatch):
    import hashlib
    import json
    from types import SimpleNamespace
    from vestigia.images import ImageService
    images = ImageService(*home, fake=True)
    image = images.ingest_bytes(animation(), filename='letters.gif')
    key = hashlib.sha256('\0'.join([image['id'], 'ocr', 'fixture-ocr', 'eng', 'direct-psm6']).encode()).hexdigest()
    images._store_interpretation(image_id=image['id'], route='ocr', model='fixture-ocr', detail='eng',
        category='general', question='Read the image', cache_key=key,
        result='FIRST FRAME ONLY', metadata={'recipe': 'direct-psm6'})
    monkeypatch.setattr(images, '_ocr_version', lambda _: 'fixture-ocr')
    monkeypatch.setattr('vestigia.images.shutil.which', lambda _: 'fixture-ocr')
    monkeypatch.setattr('vestigia.images.subprocess.run', lambda *a, **k: SimpleNamespace(
        returncode=0, stdout='ALL SAMPLED FRAMES', stderr=''))
    result = images.inspect(image['id'], routes=['ocr'])
    assert result['results'][0]['cached'] is False
    assert result['results'][0]['text'] == 'ALL SAMPLED FRAMES'
    assert result['coverage']['frame_indices'] == [0, 1, 2]
    assert images.inspect(image['id'], routes=['ocr'])['results'][0]['cached'] is True
    with home[1].connect() as connection:
        row = connection.execute("SELECT metadata_json FROM image_interpretations WHERE result_text='ALL SAMPLED FRAMES'").fetchone()
    assert json.loads(row['metadata_json'])['coverage']['frame_indices'] == [0, 1, 2]
