import pytest
from pathlib import Path
from muse_bridge.config import Config


@pytest.fixture
def cfg(tmp_path: Path):
    return Config(
        bridge_api_key='bridge-' + 'b'*40,
        siwc_proxy_api_key='upstream-' + 'u'*40,
        upstream_url='http://siwc:8787/v1',
        db_path=tmp_path/'store.sqlite3',
        default_model='', allowed_origins=frozenset({'https://example.test'}),
        max_prompt_chars=40000, max_context_chars=90000, max_calls_per_hour=100,
        max_concurrent=2, max_history_messages=24, upstream_timeout_sec=10, max_retries=1,
    )
