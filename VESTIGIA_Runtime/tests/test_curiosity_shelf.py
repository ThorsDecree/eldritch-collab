from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.home import initialize_home
from vestigia.house_tools import HousePort


def house_at(path):
    initialize_home(path, name='Curious', glyph='🌼')
    db = ContinuityDB(path / 'memory' / 'continuity.db')
    db.initialize()
    return HousePort(load_config(path), db)


def call(house, **payload):
    result, _, _ = house.registry.dispatch(payload)
    return result


def test_shelf_lifecycle_and_persistence(tmp_path):
    path = tmp_path / 'home'
    house = house_at(path)
    item = call(house, action='curiosity.add', question='Why do lanterns glow?', tags=['light'])['item']
    call(house, action='curiosity.update', curiosity_id=item['id'], status='simmering')
    other = HousePort(house.config, house.db)
    assert call(other, action='curiosity.list')['items'][0]['status'] == 'simmering'
    assert call(other, action='curiosity.show', curiosity_id=item['id'])['item']['question'] == 'Why do lanterns glow?'


def test_opt_in_and_suggestions_are_private_bounded_and_explainable(tmp_path):
    house = house_at(tmp_path / 'home')
    assert call(house, action='curiosity.settings')['passive_discovery'] is False
    call(house, action='curiosity.settings', passive_discovery=True)
    assert call(house, action='curiosity.settings')['passive_discovery'] is True
    one = call(house, action='curiosity.add', question='Why do lanterns glow?', tags=['light'])['item']
    two = call(house, action='curiosity.add', question='How does light travel?', tags=['light'])['item']
    result = call(house, action='serendipity.suggest', curiosity_id=one['id'])
    assert result['suggestions'][0]['item']['id'] == two['id']
    assert result['suggestions'][0]['shared_tags'] == ['light']
    assert result['privacy'] == 'resident_private'
    call(house, action='curiosity.update', curiosity_id=two['id'], status='closed')
    assert call(house, action='serendipity.suggest', curiosity_id=one['id'])['suggestions'] == []


def test_curation_invitation_only_when_enabled(tmp_path):
    from vestigia.curiosity_shelf import curation_invitation
    house = house_at(tmp_path / 'home')
    assert curation_invitation(house) == ''
    call(house, action='curiosity.settings', passive_discovery=True)
    assert 'curiosity.add' in curation_invitation(house)
    call(house, action='curiosity.settings', passive_discovery=False)
    assert curation_invitation(house) == ''


def test_workbench_card_opens_shelf_and_stale_card_is_rejected(tmp_path):
    import pytest
    house = house_at(tmp_path / 'home')
    item = call(house, action='curiosity.add', question='Where does light go?')['item']
    view = call(house, action='workbench.view', lane='tend')
    card = next(c for c in view['cards'] if c['provider'] == 'curiosity.shelf')
    result = call(house, action='workbench.act', card_id=card['card_id'], action_id='open')
    assert result['outcome']['item']['id'] == item['id']
    call(house, action='curiosity.update', curiosity_id=item['id'], status='closed')
    with pytest.raises(KeyError, match='stale'):
        call(house, action='workbench.act', card_id=card['card_id'], action_id='open')


def test_edit_and_scope_boundaries(tmp_path):
    import pytest
    house = house_at(tmp_path / 'home')
    item = call(house, action='curiosity.add', question='Original?', tags=['LIGHT'])['item']
    changed = call(house, action='curiosity.update', curiosity_id=item['id'], question='Revised?', tags=['Sky'])['item']
    assert changed['question'] == 'Revised?'
    assert changed['tags'] == ['sky']
    house.resident_id = 'another-resident'
    assert call(house, action='curiosity.list')['items'] == []
    with pytest.raises(KeyError):
        call(house, action='curiosity.show', curiosity_id=item['id'])
    assert call(house, action='curiosity.settings')['passive_discovery'] is False


def test_opted_out_never_invites_discovery_and_suggestions_exclude_other_residents(tmp_path):
    from vestigia.curiosity_shelf import curation_invitation
    house = house_at(tmp_path / 'home')
    mine = call(house, action='curiosity.add', question='What glows?', tags=['light'])['item']
    house.resident_id = 'someone-else'
    call(house, action='curiosity.add', question='Another light?', tags=['light'])
    call(house, action='curiosity.settings', passive_discovery=True)
    house.resident_id = 'curious'
    # Use the real resident ID, which can differ from the display name.
    house.resident_id = str(house.config.get('resident.id'))
    assert curation_invitation(house) == ''
    assert call(house, action='serendipity.suggest', curiosity_id=mine['id'])['suggestions'] == []


def test_disabled_house_does_not_offer_unusable_shelf_cards(tmp_path):
    house = house_at(tmp_path / 'home')
    call(house, action='curiosity.add', question='What glows?')
    house.config.data['house']['enabled'] = False
    view = call(house, action='workbench.view', lane='tend')
    assert not [card for card in view['cards'] if card['provider'] == 'curiosity.shelf']
