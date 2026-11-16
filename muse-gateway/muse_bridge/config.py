"""Configuration with fail-closed secrets and bounded resource consumption."""
from dataclasses import dataclass
from pathlib import Path
import os


def env_int(name: str, default: int, lower: int, upper: int) -> int:
    try:
        v = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not lower <= v <= upper:
        raise ValueError(f"{name} must be between {lower} and {upper}")
    return v


@dataclass(frozen=True)
class Config:
    bridge_api_key: str
    siwc_proxy_api_key: str
    upstream_url: str
    db_path: Path
    default_model: str
    allowed_origins: frozenset[str]
    max_prompt_chars: int
    max_context_chars: int
    max_calls_per_hour: int
    max_concurrent: int
    max_history_messages: int
    upstream_timeout_sec: int
    max_retries: int

    @classmethod
    def from_env(cls, *, require_bridge_key: bool = True) -> "Config":
        bridge = os.getenv("BRIDGE_API_KEY", "")
        upstream = os.getenv("SIWC_PROXY_API_KEY", "")
        if require_bridge_key and len(bridge) < 24:
            raise ValueError("BRIDGE_API_KEY must be set to a random secret of >=24 characters")
        if len(upstream) < 16:
            raise ValueError("SIWC_PROXY_API_KEY must be set to >=16 characters")
        if any(placeholder in secret for secret in (bridge, upstream) for placeholder in ("REPLACE_WITH", "CHANGE_ME", "YOUR_SECRET")):
            raise ValueError("Replace all sample keys with randomly generated secrets")
        url = os.getenv("SIWC_PROXY_URL", "http://127.0.0.1:8787/v1").rstrip("/")
        if not (url.startswith("http://127.0.0.1:") or url.startswith("http://localhost:") or url.startswith("http://siwc:") or url.startswith("https://")):
            raise ValueError("SIWC_PROXY_URL must be local Docker/loopback HTTP or HTTPS")
        return cls(
            bridge_api_key=bridge,
            siwc_proxy_api_key=upstream,
            upstream_url=url,
            db_path=Path(os.getenv("BRIDGE_DB_PATH", "/data/bridge.sqlite3")),
            default_model=os.getenv("BRIDGE_DEFAULT_MODEL", ""),
            allowed_origins=frozenset(x.strip().rstrip("/") for x in os.getenv("BRIDGE_ALLOWED_ORIGINS", "").split(",") if x.strip()),
            max_prompt_chars=env_int("BRIDGE_MAX_PROMPT_CHARS", 40000, 1, 200000),
            max_context_chars=env_int("BRIDGE_MAX_CONTEXT_CHARS", 90000, 1000, 500000),
            max_calls_per_hour=env_int("BRIDGE_MAX_CALLS_PER_HOUR", 100, 1, 10000),
            max_concurrent=env_int("BRIDGE_MAX_CONCURRENT", 2, 1, 20),
            max_history_messages=env_int("BRIDGE_MAX_HISTORY_MESSAGES", 24, 2, 100),
            upstream_timeout_sec=env_int("BRIDGE_UPSTREAM_TIMEOUT_SEC", 240, 10, 1200),
            max_retries=env_int("BRIDGE_MAX_RETRIES", 1, 0, 3),
        )
