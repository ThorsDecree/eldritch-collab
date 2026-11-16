import pytest
from tests.test_curiosity_shelf import house_at, call


def test_explicit_shelf_search_returns_cited_passages_without_shared_tags(tmp_path):
    house = house_at(tmp_path / 'home')
    (house.home / 'workspace' / 'lantern.md').write_text('# Lanterns\nA lantern glows at dusk.', encoding='utf-8')
    item = call(house, action='curiosity.add', question='Why do lanterns glow?')['item']
    result = call(house, action='serendipity.explore', curiosity_id=item['id'], scope='workspace', terms=['lantern'])
    passage = result['suggestions'][0]
    assert passage['path'] == 'workspace/lantern.md'
    assert 'lantern' in passage['excerpt'].lower()
    assert passage['file_hash'] and passage['chunk_index'] == 0
    assert passage['matched_terms'] == ['lantern']
    assert result['privacy'] == 'resident_private'
    assert result['automatic'] is False


def test_scope_is_explicit_and_no_cross_shelf_leak(tmp_path):
    house = house_at(tmp_path / 'home')
    (house.home / 'workspace' / 'public.md').write_text('Lantern on desk', encoding='utf-8')
    (house.home / 'identity' / 'private.md').write_text('Lantern secret', encoding='utf-8')
    item = call(house, action='curiosity.add', question='Lantern?')['item']
    result = call(house, action='serendipity.explore', curiosity_id=item['id'], scope='workspace', terms=['lantern'])
    assert all(x['path'].startswith('workspace/') for x in result['suggestions'])
    for scope in ('', '%', '../identity', 'workspace/../identity'):
        with pytest.raises((ValueError, PermissionError)):
            call(house, action='serendipity.explore', curiosity_id=item['id'], scope=scope, terms=['lantern'])


def test_closed_question_requires_reopening(tmp_path):
    house = house_at(tmp_path / 'home')
    item = call(house, action='curiosity.add', question='Lantern?')['item']
    call(house, action='curiosity.update', curiosity_id=item['id'], status='closed')
    with pytest.raises(ValueError, match='closed'):
        call(house, action='serendipity.explore', curiosity_id=item['id'], scope='workspace', terms=['lantern'])


def test_search_terms_deduplicate_and_results_track_all_matches(tmp_path):
    house = house_at(tmp_path / 'home')
    (house.home / 'workspace' / 'one.md').write_text('Lantern light and lantern light.', encoding='utf-8')
    item = call(house, action='curiosity.add', question='What glows?')['item']
    result = call(house, action='serendipity.explore', curiosity_id=item['id'], scope='workspace', terms=['LANTERN', 'light', 'lantern'])
    assert result['terms'] == ['lantern', 'light']
    assert len(result['suggestions']) == 1
    assert result['suggestions'][0]['matched_terms'] == ['lantern', 'light']
    assert len(result['search_receipts']) == 2


def test_removed_root_and_malformed_terms_are_rejected(tmp_path):
    house = house_at(tmp_path / 'home')
    item = call(house, action='curiosity.add', question='Lantern?')['item']
    house.config.data['house']['accessible_roots'] = ['identity']
    with pytest.raises(PermissionError):
        call(house, action='serendipity.explore', curiosity_id=item['id'], scope='workspace', terms=['lantern'])
    for term in ['a', 'lantern OR moon', '---']:
        with pytest.raises(ValueError):
            call(house, action='serendipity.explore', curiosity_id=item['id'], scope='identity', terms=[term])


def test_candidate_truncation_is_reported(tmp_path):
    house = house_at(tmp_path / 'home')
    for i in range(21):
        (house.home / 'workspace' / f'light-{i}.md').write_text('lantern', encoding='utf-8')
    item = call(house, action='curiosity.add', question='Where is light?')['item']
    result = call(house, action='serendipity.explore', curiosity_id=item['id'], scope='workspace', terms=['lantern'], limit=20)
    assert len(result['suggestions']) == 20
    assert result['possibly_truncated_terms'] == ['lantern']
