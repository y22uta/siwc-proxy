import json
import pytest
import httpx
from muse_bridge.service import BridgeService, BridgeError
from muse_bridge.protocol import handle_rpc, TOOLS
from muse_bridge.http_app import create_app


class Backend:
    def __init__(self):
        self.calls = []
        self.fail = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith('/models'):
            return httpx.Response(200, json={'data': [{'id': 'demo-model', 'display_name': 'Demo'}]})
        if str(request.url).endswith('/responses'):
            assert request.headers['authorization'].startswith('Bearer upstream-')
            body = json.loads(request.content)
            assert body['store'] is False
            assert body['stream'] is False
            self.calls.append(body)
            if self.fail:
                return httpx.Response(429, json={'error': {'message': 'usage limit'}})
            return httpx.Response(200, json={'status': 'completed', 'output': [
                {'type': 'message', 'content': [{'type':'output_text', 'text': 'テスト回答'}]}
            ]})
        return httpx.Response(404)


@pytest.fixture
def runner(cfg):
    backend = Backend()
    client = httpx.AsyncClient(transport=httpx.MockTransport(backend),
                               headers={'Authorization': f'Bearer {cfg.siwc_proxy_api_key}'})
    service = BridgeService(cfg, client)
    return service, backend


@pytest.mark.asyncio
async def test_models_and_writing(runner):
    service, backend = runner
    assert (await service.models())[0]['id'] == 'demo-model'
    reply = await service.article(topic='オートミール', approx_chars=1800)
    assert reply['answer'] == 'テスト回答'
    assert reply['mode'] == 'writing'
    assert backend.calls[0]['model'] == 'demo-model'
    assert 'オートミール' in backend.calls[0]['input'][-1]['content']


@pytest.mark.asyncio
async def test_session_history_idempotency(runner):
    service, backend = runner
    sess = (await service.dispatch('chatgpt_session_create', {'label':'Article'}))['session_id']
    first = await service.ask(prompt='first', mode='writing', session_id=sess, request_id='client-1')
    replay = await service.ask(prompt='first', mode='writing', session_id=sess, request_id='client-1')
    assert replay['replayed'] is True
    assert replay['answer'] == first['answer']
    assert len(backend.calls) == 1
    await service.ask(prompt='second', mode='writing', session_id=sess, request_id='client-2')
    assert len(backend.calls[1]['input']) == 4
    assert len(service.store.history(sess)) == 4
    assert service.store.delete_session(sess)
    assert service.store.history(sess) == []


@pytest.mark.asyncio
async def test_mcp_authorization_initialize_and_call(cfg, runner):
    service, _ = runner
    app = create_app(cfg, service)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://localhost') as client:
        init = {'jsonrpc':'2.0', 'id':1, 'method':'initialize', 'params':{'protocolVersion':'2025-06-18'}}
        assert (await client.post('/mcp', json=init)).status_code == 401
        headers = {'Authorization': f'Bearer {cfg.bridge_api_key}'}
        r = await client.post('/mcp', json=init, headers=headers)
        assert r.status_code == 200
        assert r.json()['result']['capabilities']['tools']
        r = await client.post('/mcp', json={'jsonrpc':'2.0','id':2,'method':'tools/list'}, headers=headers)
        assert r.json()['result']['tools'] == TOOLS
        r = await client.post('/mcp', json={'jsonrpc':'2.0','id':3,'method':'tools/call','params':{
            'name':'chatgpt_ask','arguments':{'prompt':'日本語で短く'}}}, headers=headers)
        result = r.json()['result']
        assert result['isError'] is False
        assert result['structuredContent']['answer'] == 'テスト回答'
        r = await client.post('/mcp', json=init, headers={**headers, 'Origin':'https://evil.test'})
        assert r.status_code == 403
        r = await client.post('/mcp', json=init, headers={**headers, 'Origin':'https://example.test'})
        assert r.status_code == 200
        r = await client.post('/mcp', json={'jsonrpc':'2.0','method':'notifications/initialized'}, headers=headers)
        assert r.status_code == 202


@pytest.mark.asyncio
async def test_errors_rate_limit_and_unavailable_model(runner):
    service, backend = runner
    with pytest.raises(BridgeError) as err:
        await service.ask(prompt='hi', model='not-eligible')
    assert err.value.code == 'model_unavailable'
    backend.fail = True
    with pytest.raises(BridgeError) as err:
        await service.ask(prompt='hi')
    assert err.value.code == 'plan_usage_limited'
    assert service.store.stats()['calls_last_hour'] == 1
    service.config = __import__('dataclasses').replace(service.config, max_calls_per_hour=1)
    with pytest.raises(BridgeError) as err:
        await service.ask(prompt='again')
    assert err.value.code == 'local_rate_limited'
    assert len(backend.calls) == 1


@pytest.mark.asyncio
async def test_rpc_invalid_inputs(runner):
    service, _ = runner
    r = await handle_rpc(service, {'jsonrpc':'2.0','id':5,'method':'tools/call', 'params':{'name':'chatgpt_ask','arguments':{'prompt':''}}})
    assert r['result']['isError'] is True
    assert r['result']['content'][0]['type'] == 'text'
    r = await handle_rpc(service, {'jsonrpc':'2.0','id':6,'method':'not-a-method'})
    assert r['error']['code'] == -32601


@pytest.mark.asyncio
async def test_rest_endpoints(cfg, runner):
    service, _ = runner
    app = create_app(cfg, service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost') as client:
        assert (await client.get('/healthz')).status_code == 200
        head = {'Authorization': f'Bearer {cfg.bridge_api_key}'}
        r = await client.post('/api/tools/chatgpt_status', headers=head, json={})
        assert r.status_code == 200
        assert r.json()['connected'] is True
        r = await client.post('/api/tools/chatgpt_ask', headers=head, json={'prompt':'hi'})
        assert r.json()['answer'] == 'テスト回答'

@pytest.mark.asyncio
async def test_mcp_2026_header_consistency(cfg, runner):
    service, _ = runner
    app = create_app(cfg, service)
    msg = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
           'params': {'name': 'chatgpt_models', 'arguments': {},
                      '_meta': {'io.modelcontextprotocol/protocolVersion': '2026-07-28'}}}
    headers = {'Authorization': f'Bearer {cfg.bridge_api_key}',
               'MCP-Protocol-Version': '2026-07-28',
               'Mcp-Method': 'tools/call', 'Mcp-Name': 'chatgpt_models'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost') as c:
        assert (await c.post('/mcp', headers=headers, json=msg)).status_code == 200
        assert (await c.post('/mcp', headers={**headers, 'Mcp-Name':'evil'}, json=msg)).status_code == 400
        assert (await c.post('/mcp', headers={**headers, 'MCP-Protocol-Version':'1990-01-01'}, json=msg)).status_code == 400

@pytest.mark.asyncio
async def test_session_mode_default(runner):
    service, backend = runner
    sid = service.store.new_session('writing', 'My article')['session_id']
    response = await service.ask(prompt='Rewrite paragraph', session_id=sid)
    assert response['mode'] == 'writing'
    assert 'publication-quality' in backend.calls[0]['input'][0]['content']
