# Muse / Manus × ChatGPT Plus — SIWC Bridge

**Self-hosted, single-user Model Context Protocol gateway** for an agent (Manus/Muse) to request writing, analysis, research synthesis, code review and persistent conversations from models that the user's own **Sign in with ChatGPT plan usage** account exposes.

**No OpenAI API key is needed:** inference is delegated to [`d-nyamkhuu/siwc-proxy`](https://github.com/d-nyamkhuu/siwc-proxy) **v0.2.3**, which manages official Sign in with ChatGPT OAuth and forwards to the public Responses API. Subscription allowances and eligible models are determined by OpenAI when the user signs in. *Not* a ChatGPT web-app session, shared ChatGPT history or unrestricted API allowance.

This repository is a **companion gateway to upstream `siwc-proxy`, not a published GitHub fork**. The upstream is installed as a pinned dependency in a private Docker service; its MIT license remains upstream's. This boundary deliberately keeps OAuth/token handling in the original maintained project and feature development in this application.

## Architecture

```text
Manus (HTTPS + Bearer token)
        |
    private tunnel / TLS reverse proxy
        |
  /mcp or /api/tools/<name>
  [muse-siwc-bridge] — SQLite (sessions, history, idempotency, rate budget)
        |
  internal Docker network /v1/responses
        |
    [siwc-proxy 0.2.3] — OAuth secrets in a DIFFERENT Docker volume
        |
  public https://api.openai.com/v1/responses
        |
    eligible ChatGPT plan usage
```

No direct access to OpenAI OAuth tokens is exposed to the MCP clients. The SIWC service is **not published to the host**, and the bridge binds to **127.0.0.1 only** in the default Compose configuration. Never directly expose the `siwc` service.

## Features (implemented)

- **Nine MCP tools**: `chatgpt_ask`, `chatgpt_article`, `chatgpt_review`, `chatgpt_models`, `chatgpt_status`, `chatgpt_session_create`, `chatgpt_sessions`, `chatgpt_history`, `chatgpt_session_delete`.
- Persistent SQLite history with contextual follow-up questions; independent conversation sessions.
- Multi-purpose presets: general, writing, analysis, research (based on supplied sources), code, review.
- Per-account live model catalog and validation (five-minute cache), selected model override.
- Hourly local request limit, max concurrent requests, bounded prompt/context and timeouts.
- Idempotency: `request_id` prevents successful session-based requests from being repeated after a client retransmit.
- Non-sensitive status and usage counters; errors identify authentication vs plan limits.
- Authenticated MCP Streamable HTTP (2025-03-26 / 2025-06-18 / 2025-11-25 / 2026-07-28 via `POST /mcp`), local MCP stdio CLI and REST tools endpoint.
- Docker Compose, health endpoint, local data volumes, automated tests and CI.

**Deliberate boundaries:** no web browsing is performed by the bridge, no fabricated URLs/citations; supply sources from Manus. No automatic retries after a response timeout (could consume duplicate plan usage). A 429 (subscription usage limit) is not bypassed. Manus may perform final publication using its existing WordPress tooling; this bridge creates the article text, not the website upload.

## 1. Start from your Windows WSL2 / Docker host

```bash
cp .env.example .env
python - <<'PY'
import secrets
print('BRIDGE_API_KEY=' + secrets.token_urlsafe(48))
print('SIWC_PROXY_API_KEY=' + secrets.token_urlsafe(48))
PY
```

Paste the generated values into `.env`, replacing the placeholders. Use a distinct secret for each one. Keep `.env` private.

```bash
docker compose up -d --build
# Follow the official OAuth flow ONCE to authorize this locally hosted instance:
docker compose run --rm siwc siwc-proxy login --remote
# Follow the URL and terminal instructions; this stores credentials in siwc_credentials.
# Recreate services (the already running siwc instance will pick up the shared credential volume on future requests):
docker compose restart siwc
curl http://127.0.0.1:8088/healthz
```

**Test real model access locally** (do not commit tokens):

```bash
set -a; . ./.env; set +a
curl -fsS -X POST http://127.0.0.1:8088/api/tools/chatgpt_models \
  -H "Authorization: Bearer $BRIDGE_API_KEY" -H 'Content-Type: application/json' -d '{}'

curl -fsS -X POST http://127.0.0.1:8088/api/tools/chatgpt_ask \
  -H "Authorization: Bearer $BRIDGE_API_KEY" -H 'Content-Type: application/json' \
  -d '{"prompt":"炭水化物の役割をわかりやすく200文字で説明して","mode":"writing"}'
```

The model is chosen from the live account-specific catalog; you can set `BRIDGE_DEFAULT_MODEL` only to a model shown by `chatgpt_models`.

## 2. Connect remote Manus/Muse

Manus cannot reach `127.0.0.1` on your home PC. Provide a private, **HTTPS** entry point to the bridge using your existing reverse-proxy/tunnel service. Keep the SIWC proxy internal. The TLS service must forward `Authorization` and `MCP-Protocol-Version` request headers unchanged, and route `/mcp` to `127.0.0.1:8088`. Protect access with the Bearer token in addition to TLS. Configure `BRIDGE_ALLOWED_ORIGINS` only if the client sends an `Origin` header (list exact allowed HTTPS origins comma-separated).

In Manus custom MCP settings:

- **URL:** `https://YOUR-PRIVATE-HOST/mcp`
- **Transport:** Streamable HTTP
- **Authorization header:** `Bearer <your BRIDGE_API_KEY>` (header name `Authorization`)

You must provide your own hostname/tunnel. The bridge intentionally does not auto-publish your PC or distribute your OAuth credentials.

**Note on availability / scope:** OpenAI's published SIWC flow covers open-source/local and self-hosted applications; hosted commercial/remote offerings may require additional approval. Check your specific deployment against the latest docs: <https://developers.openai.com/siwc/token-sharing-open-source>. This project is designed for one owner on their own self-hosted machine, not an account-sharing SaaS.

## 3. Example MCP use from Manus

1. Invoke `chatgpt_status` and `chatgpt_models` to inspect model eligibility.
2. Invoke `chatgpt_session_create` with `{"label":"SEOブログ記事", "mode":"writing"}`; retain `session_id`.
3. Invoke `chatgpt_article` with topic/audience/keywords/sources and `session_id`.
4. Invoke `chatgpt_ask` with a follow-up instruction and the same `session_id` to revise the article.
5. `chatgpt_history` can retrieve your saved conversation; `chatgpt_session_delete` clears it.

### REST API examples

`POST /api/tools/chatgpt_article`:

```json
{
  "topic":"筋トレ時の炭水化物の役割",
  "audience":"筋トレ初心者",
  "tone":"読みやすく具体的",
  "approx_chars":3000,
  "keywords":"筋トレ, 炭水化物, 回復",
  "sources":"調査資料をManusがここへ添付"
}
```

`POST /api/tools/chatgpt_ask`:

```json
{"prompt":"前回の内容を初心者向けに書き直して", "session_id":"...", "request_id":"unique-client-request-001", "mode":"writing"}
```

## Local MCP stdio alternative

Without exposing a port, run the original `siwc-proxy serve` locally and then:

```bash
pip install -e .
export SIWC_PROXY_API_KEY='same-local-upstream-key'
export SIWC_PROXY_URL='http://127.0.0.1:8787/v1'
export BRIDGE_DB_PATH="$HOME/.local/share/muse-bridge/bridge.sqlite3"
muse-siwc-mcp
```

Configure your local MCP client to launch `muse-siwc-mcp` with these environment variables; stdio does not require `BRIDGE_API_KEY`. Cloud Manus requires the HTTPS MCP transport above.

## Security, tests, maintenance

- Default bridge requires `BRIDGE_API_KEY` (>=24 chars), checked with `hmac.compare_digest`.
- An `Origin` header is rejected unless allowlisted; absence is accepted for non-browser clients.
- Never place OAuth tokens in Manus, repo, prompt or logs. Separate Docker volume for SIWC tokens.
- `request_id` is idempotent for **successful** calls with a `session_id`, not for aborted in-flight calls.
- Stored prompts, replies and request results are plaintext in SQLite on your own machine. Protect backups and local access.
- Single process worker is the default; for multiple workers, session-level synchronization must be redesigned. Database request-count reservations are atomic, but in-memory session locks are per process.
- `docker compose logs --tail=100 bridge`, `docker compose logs --tail=100 siwc`; if authorization expires, rerun `docker compose run --rm siwc siwc-proxy login --remote`.
- To test without a real subscription: `pip install -e '.[test]' && pytest -q`.
- To update official SIWC plumbing later: inspect upstream changes, update pin in `docker/Dockerfile.siwc`, run the test suite and test OAuth with your own account.

### References

- Upstream: <https://github.com/d-nyamkhuu/siwc-proxy>
- Official OpenAI SIWC: <https://developers.openai.com/siwc/token-sharing-open-source>
- Models and inference: <https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference>
- MCP Streamable HTTP: <https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http>

**License:** MIT for this gateway. Upstream `siwc-proxy` is a separate MIT-licensed dependency. No upstream source files are copied into this repository.

## Publish this gateway inside an official GitHub fork

This working directory is an independently packaged gateway. To preserve an actual GitHub fork relationship and the full upstream source/history, use the bundled publisher **from your own authenticated computer**. If `gh` CLI is available and signed in, it automatically creates the GitHub fork when absent. If it is unavailable, first create the fork via <https://github.com/d-nyamkhuu/siwc-proxy/fork>.

```bash
# After extracting this project archive:
bash scripts/publish-to-fork.sh y22uta/siwc-proxy
```

The script clones the real GitHub fork and commits the bridge under `muse-gateway/`. It refuses to overwrite an existing folder. The result is a proper `siwc-proxy` fork with this entire gateway present. Run Docker Compose from the `muse-gateway/` directory.

**Do not** use `git init` + push as a substitute for GitHub fork creation if you want GitHub to preserve the fork relationship.
