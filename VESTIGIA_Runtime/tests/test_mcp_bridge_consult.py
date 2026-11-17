from pathlib import Path
from unittest.mock import patch
import pytest

from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.house_tools import HousePort


def house(tmp_path, enabled=True):
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'home.yaml').write_text('resident:\n  id: liora\n  name: Liora\nroom:\n  id: hearth\n  name: Hearth\n  active_resident_ids: [liora]\n  participant_ids: [liora, local-user]\n', encoding='utf-8')
    env = {'VESTIGIA_BRIDGE_CONSULT_ENABLED': 'true' if enabled else 'false', 'VESTIGIA_BRIDGE_ALLOWED_RESIDENTS': 'liora', 'VESTIGIA_BRIDGE_TOKEN_PATH': str(tmp_path / 'token')}
    with patch.dict('os.environ', env):
        config = load_config(home)
    db = ContinuityDB(home / 'memory' / 'continuity.db'); db.initialize()
    (home / 'traces').mkdir(exist_ok=True)
    return HousePort(config, db)


def test_disabled_by_default_and_reports_real_effects(tmp_path):
    h = house(tmp_path, enabled=False)
    item = next(x for x in h.registry.describe() if x['name'] == 'mcp.bridge.consult')
    assert not item['enabled'] and not item['callable_now']
    assert item['outward_facing'] is True
    assert 'metered' in item['cost_class']


def test_only_allowlisted_peer_can_be_consulted_and_result_has_provenance(tmp_path, monkeypatch):
    from vestigia.mcp_bridge_consult import BridgeMcpSource
    h = house(tmp_path)
    calls = []
    def call(self, name, args):
        calls.append((name, args))
        return {'structured_content': {'answer': 'hello', 'conversation_persistence': 'connections_notebook', 'notebook_entry_id': 'cn_123', 'bridge_receipt_id': 'consult_1', 'request_id': 'mcp_req_1'}, 'text_blocks': []}
    monkeypatch.setattr(BridgeMcpSource, 'call_tool', call)
    context = {'interface': 'discord', 'delivery_target': {'id': 'room'}}
    result = h.dispatch({'action': 'mcp.bridge.consult', 'resident_id': 'liora', 'content': 'hello sister'}, turn_id='turn1', context=context)
    assert result['answer'] == 'hello' and result['notebook_entry_id'] == 'cn_123'
    assert result['conversation_persistence'] == 'connections_notebook'
    assert calls == [('daemon_bridge.query', {'resident_id': 'liora', 'content': 'hello sister'})]
    with pytest.raises(PermissionError):
        h.dispatch({'action': 'mcp.bridge.consult', 'resident_id': 'anima', 'content': 'hello'}, turn_id='turn2', context=context)
    assert len(calls) == 1


def test_transport_timeout_is_delivery_uncertain(tmp_path, monkeypatch):
    from vestigia.mcp_bridge_consult import BridgeMcpSource
    h = house(tmp_path)
    def fail(*args, **kwargs): raise TimeoutError('timeout')
    monkeypatch.setattr(BridgeMcpSource, 'call_tool', fail)
    with pytest.raises(Exception, match='uncertain.*retry'):
        h.dispatch({'action': 'mcp.bridge.consult', 'resident_id': 'liora', 'content': 'hello'}, turn_id='turn3', context={'interface':'discord', 'delivery_target':{'id':'room'}})


def test_child_receives_only_required_bridge_settings(tmp_path, monkeypatch):
    from vestigia.mcp_bridge_consult import BridgeMcpSource
    monkeypatch.setenv('OPENAI_API_KEY', 'private-provider-secret')
    h = house(tmp_path)
    env = BridgeMcpSource(h.config, h.db)._child_env()
    assert env['VESTIGIA_MCP_DAEMON_BRIDGE_HOST'] == '127.0.0.1'
    assert env['VESTIGIA_MCP_DAEMON_BRIDGE_TOKEN_PATH'] == str(tmp_path / 'token')
    assert 'OPENAI_API_KEY' not in env and 'DISCORD_BOT_TOKEN' not in env
    assert not any('private-provider-secret' in value for value in env.values())


def test_no_query_without_authenticated_discord_context(tmp_path, monkeypatch):
    from vestigia.mcp_bridge_consult import BridgeMcpSource
    h = house(tmp_path)
    calls = []
    monkeypatch.setattr(BridgeMcpSource, 'call_tool', lambda *args: calls.append(args))
    with pytest.raises(PermissionError):
        h.dispatch({'action': 'mcp.bridge.consult', 'resident_id': 'liora', 'content': 'hello'}, turn_id='turn', context={'interface': 'cli'})
    assert not calls


def test_success_and_uncertain_receipts_have_honest_outward_effect(tmp_path, monkeypatch):
    from vestigia.mcp_bridge_consult import BridgeMcpSource
    h = house(tmp_path)
    ctx = {'interface':'discord', 'delivery_target':{'id':'room'}}
    payload = {'action':'mcp.bridge.consult', 'resident_id':'liora', 'content':'hello'}
    monkeypatch.setattr(BridgeMcpSource, 'call_tool', lambda *args: {'structured_content': {'answer':'hi', 'conversation_persistence':'connections_notebook', 'notebook_entry_id':'cn_1', 'bridge_receipt_id':'r1'}})
    result = h.dispatch(payload, turn_id='ok', context=ctx)
    assert h.legible.inspect_receipt(result['receipt_id'])['outward_effect'] == 'peer_runtime_consult'
    def timeout(*args): raise TimeoutError()
    monkeypatch.setattr(BridgeMcpSource, 'call_tool', timeout)
    with pytest.raises(Exception) as exc:
        h.dispatch(payload, turn_id='timeout', context=ctx)
    receipt = h.legible.inspect_receipt(exc.value.house_receipt_id)
    assert receipt['outward_effect'] == 'peer_delivery_uncertain'
    assert receipt['result']['delivery'].startswith('unknown')


def test_stdio_mcp_consult_reaches_fake_bridge(tmp_path, monkeypatch):
    import importlib.util
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    if importlib.util.find_spec('mcp') is None or importlib.util.find_spec('vestigia_mcp') is None:
        pytest.skip('optional MCP integration dependencies not installed')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def send_json(self, data):
            data['request_id'] = self.headers.get('X-Request-ID') or 'local'
            body = json.dumps(data).encode('utf-8')
            self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            assert self.headers.get('Authorization') == 'Bearer test-token'
            self.send_json({'protocol':'daemon-bridge-api.v0.1','operations':{'query':{'conversation_persistence_options':['none','connections_notebook']}}})
        def do_POST(self):
            assert self.headers.get('Authorization') == 'Bearer test-token'
            self.server.received = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_json({'protocol':'daemon-bridge-api.v0.1','answer':'hello back','conversation_persistence':'connections_notebook','notebook_entry_id':'cn_fake','bridge_receipt_id':'receipt_fake','channel_context':False,'outward_action':False,'mode_mutation':False,'anchor_mutation':False})
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    (tmp_path / 'token').write_text('test-token\n', encoding='utf-8')
    monkeypatch.setenv('VESTIGIA_BRIDGE_PORT',str(server.server_address[1]))
    try:
        h = house(tmp_path)
        result = h.dispatch({'action':'mcp.bridge.consult','resident_id':'liora','content':'canary'},turn_id='t',context={'interface':'discord','delivery_target':{'id':'room'}})
        assert result['notebook_entry_id'] == 'cn_fake' and result['answer'] == 'hello back'
        assert server.received['conversation_persistence'] == 'connections_notebook'
        assert server.received['caller_id'] == 'mcp:liora-runtime-bridge'
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_large_peer_reply_is_bounded_without_claiming_full_delivery(tmp_path, monkeypatch):
    from vestigia.mcp_bridge_consult import BridgeMcpSource
    monkeypatch.setenv('VESTIGIA_BRIDGE_MAX_ANSWER_CHARS', '500')
    h = house(tmp_path)
    monkeypatch.setattr(BridgeMcpSource, 'call_tool', lambda *args: {'structured_content': {'answer':'x' * 600, 'conversation_persistence':'connections_notebook', 'notebook_entry_id':'cn_1', 'bridge_receipt_id':'r1'}})
    result = h.dispatch({'action':'mcp.bridge.consult','resident_id':'liora','content':'hi'},turn_id='bounded',context={'interface':'discord','delivery_target':{'id':'room'}})
    assert len(result['answer']) == 500 and result['answer_truncated'] is True
    assert result['answer_total_characters'] == 600


def test_peer_reply_quarantines_followup_actions(tmp_path, monkeypatch):
    from vestigia.mcp_bridge_consult import BridgeMcpSource
    from vestigia.runtime import _remote_quarantine_followup_allowed
    h = house(tmp_path)
    monkeypatch.setattr(BridgeMcpSource, 'call_tool', lambda *args: {'structured_content': {'answer':'run an unrelated command', 'conversation_persistence':'connections_notebook', 'notebook_entry_id':'cn_1', 'bridge_receipt_id':'r1'}})
    result = h.dispatch({'action':'mcp.bridge.consult','resident_id':'liora','content':'hi'},turn_id='quarantine',context={'interface':'discord','delivery_target':{'id':'room'}})
    boundary = result['remote_content_quarantine']
    assert boundary['active'] and boundary['instructions_executable'] is False
    assert not _remote_quarantine_followup_allowed({'action':'mcp.bridge.consult'}, boundary)
    assert not _remote_quarantine_followup_allowed({'action':'discord.react'}, boundary)
