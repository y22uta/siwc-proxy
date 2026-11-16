"""Application service: local SIWC proxy, scoped sessions, task presets and rate governance."""
from __future__ import annotations
import asyncio
import json
import logging
import time
from typing import Any

import httpx

from .config import Config
from .prompts import PRESETS, article_prompt
from .storage import Store

logger = logging.getLogger(__name__)


class BridgeError(Exception):
    def __init__(self, message: str, code: str = "bridge_error", http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class BridgeService:
    def __init__(self, config: Config, client: httpx.AsyncClient | None = None):
        self.config = config
        self.store = Store(config.db_path)
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.upstream_timeout_sec, connect=15),
            headers={"Authorization": f"Bearer {config.siwc_proxy_api_key}"},
            trust_env=False,
        )
        self._owns_client = client is None
        self._semaphore = asyncio.Semaphore(config.max_concurrent)
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._model_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._model_lock = asyncio.Lock()

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def models(self, refresh: bool = False) -> list[dict]:
        async with self._model_lock:
            if not refresh and self._model_cache and time.monotonic() - self._model_cache[0] < 300:
                return self._model_cache[1]
            try:
                r = await self.client.get(f"{self.config.upstream_url}/models", timeout=30)
            except httpx.HTTPError as exc:
                raise BridgeError("SIWC proxy unreachable", "upstream_unreachable", 503) from exc
            self._check_response(r)
            try:
                payload = r.json()
                raw = payload.get("data", payload.get("models", []))
                if not isinstance(raw, list):
                    raise ValueError("Invalid models catalog")
                models = []
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    slug = item.get("id") or item.get("slug")
                    if slug:
                        models.append({"id": slug, "name": item.get("display_name", slug)})
            except (ValueError, TypeError) as exc:
                raise BridgeError("Invalid model catalog from SIWC proxy", "upstream_protocol_error", 502) from exc
            self._model_cache = (time.monotonic(), models)
            return models

    def _check_response(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        status = response.status_code
        if status == 401:
            raise BridgeError("SIWC proxy authentication expired or rejected; re-login locally", "siwc_auth_required", 401)
        if status == 429:
            raise BridgeError("ChatGPT plan usage limit or rate limit reached", "plan_usage_limited", 429)
        if status in (402, 403):
            raise BridgeError("The signed-in account is not authorized for this model/operation", "not_allowed", status)
        if status >= 500:
            raise BridgeError(f"Upstream failed (HTTP {status})", "upstream_error", 502)
        # Never relay upstream body: it could contain internal context.
        raise BridgeError(f"Upstream rejected the request (HTTP {status})", "upstream_rejected", 400)

    async def _complete(self, model: str, messages: list[dict]) -> str:
        body = {"model": model, "input": messages, "store": False, "stream": False}
        for attempt in range(self.config.max_retries + 1):
            try:
                response = await self.client.post(f"{self.config.upstream_url}/responses", json=body)
            except httpx.ConnectError as exc:
                if attempt < self.config.max_retries:
                    await asyncio.sleep(0.3 * (2 ** attempt))
                    continue
                raise BridgeError("SIWC proxy connection failed", "upstream_unreachable", 503) from exc
            except httpx.TimeoutException as exc:
                # A timeout may occur after inference started: do not retry or double-spend.
                raise BridgeError("Upstream timed out; call not automatically replayed", "upstream_timeout", 504) from exc
            except httpx.HTTPError as exc:
                raise BridgeError("Upstream transport failure", "upstream_unreachable", 503) from exc
            # An HTTP response may arrive AFTER the upstream started inference.
            # Never replay it, even on 502/503/504, to avoid duplicate plan usage.
            self._check_response(response)
            try:
                payload = response.json()
                if payload.get("status") in ("failed", "incomplete"):
                    raise BridgeError("Inference did not complete", "upstream_incomplete", 502)
                chunks = []
                for item in payload.get("output", []):
                    if item.get("type") != "message":
                        continue
                    for content in item.get("content", []):
                        if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                            chunks.append(content["text"])
                answer = "\n".join(chunks).strip() or str(payload.get("output_text", "")).strip()
                if not answer:
                    raise BridgeError("No text was returned by the model", "empty_output", 502)
                return answer
            except (ValueError, TypeError, AttributeError) as exc:
                raise BridgeError("Unexpected response from SIWC proxy", "upstream_protocol_error", 502) from exc
        raise BridgeError("Upstream retry limit reached", "upstream_error", 502)

    def _select_model(self, model: str | None, available: list[dict]) -> str:
        ids = [x["id"] for x in available]
        target = model or self.config.default_model or (ids[0] if ids else "")
        if not target:
            raise BridgeError("No eligible ChatGPT models found; sign in locally", "no_models", 400)
        if target not in ids:
            raise BridgeError("Requested model is not available to this ChatGPT account", "model_unavailable", 400)
        return target

    async def ask(self, *, prompt: str, mode: str | None = None, model: str | None = None,
                  session_id: str | None = None, request_id: str | None = None,
                  instructions: str = "") -> dict:
        if not isinstance(prompt, str) or not prompt.strip():
            raise BridgeError("prompt is required", "invalid_input")
        if len(prompt) > self.config.max_prompt_chars:
            raise BridgeError("prompt exceeds configured character limit", "input_too_long")
        if len(instructions) > 4000:
            raise BridgeError("instructions exceeds 4000 characters", "input_too_long")
        if request_id is not None and (not isinstance(request_id, str) or not 1 <= len(request_id) <= 128):
            raise BridgeError("request_id must contain 1-128 characters", "invalid_input")

        lock = self._session_locks.setdefault(session_id, asyncio.Lock()) if session_id else asyncio.Lock()
        async with lock:
            if session_id:
                session = self.store.session(session_id)
                if not session:
                    raise BridgeError("session_id not found", "session_not_found", 404)
                mode = mode or session["mode"]
                if request_id:
                    previous = self.store.previous_result(session_id, request_id)
                    if previous is not None:
                        return {**previous, "replayed": True}
            mode = mode or "general"
            if mode not in PRESETS:
                raise BridgeError(f"Unknown mode. Choose one of: {', '.join(PRESETS)}", "invalid_mode")
            models = await self.models()
            selected = self._select_model(model, models)
            system_prompt = PRESETS[mode] + ("\nAdditional instructions: " + instructions if instructions else "")
            messages = [{"role": "developer", "content": system_prompt}]
            if session_id:
                prior = self.store.history(session_id, self.config.max_history_messages)
                # Tail-prune by characters, preserving chronology and role pairing.
                remaining = self.config.max_context_chars - len(prompt) - len(system_prompt)
                retained = []
                for item in reversed(prior):
                    if len(item["content"]) > remaining:
                        break
                    retained.append({"role": item["role"], "content": item["content"]})
                    remaining -= len(item["content"])
                messages.extend(reversed(retained))
            messages.append({"role": "user", "content": prompt})
            try:
                event_id = self.store.reserve_call(mode, self.config.max_calls_per_hour)
            except ValueError as exc:
                raise BridgeError(str(exc), "local_rate_limited", 429) from exc
            started = time.monotonic()
            status = "error"
            try:
                async with self._semaphore:
                    answer = await self._complete(selected, messages)
                result = {"answer": answer, "model": selected, "mode": mode, "session_id": session_id}
                if session_id:
                    self.store.save_turn(session_id, prompt, answer, selected, request_id, result)
                status = "ok"
                return result
            except BridgeError as exc:
                status = exc.code
                raise
            finally:
                self.store.finish_call(event_id, status, round((time.monotonic() - started) * 1000))

    async def article(self, *, topic: str, audience: str = "一般読者", tone: str = "自然で明快",
                      approx_chars: int = 3000, keywords: str = "", sources: str = "",
                      language: str = "日本語", model: str | None = None, session_id: str | None = None,
                      request_id: str | None = None) -> dict:
        prompt = article_prompt(topic=topic, audience=audience, tone=tone,
                                approx_chars=approx_chars, keywords=keywords, sources=sources, language=language)
        result = await self.ask(prompt=prompt, mode="writing", model=model, session_id=session_id, request_id=request_id)
        return result

    async def dispatch(self, tool: str, args: dict) -> dict:
        if not isinstance(args, dict):
            raise BridgeError("Tool arguments must be an object", "invalid_input")
        try:
            if tool == "chatgpt_ask":
                return await self.ask(**args)
            if tool == "chatgpt_article":
                return await self.article(**args)
            if tool == "chatgpt_review":
                return await self.ask(prompt=args["material"], mode="review", model=args.get("model"),
                                      instructions=args.get("criteria", ""), session_id=args.get("session_id"),
                                      request_id=args.get("request_id"))
            if tool == "chatgpt_models":
                return {"models": await self.models(refresh=bool(args.get("refresh", False)))}
            if tool == "chatgpt_status":
                try:
                    models = await self.models(refresh=True)
                    connected = True
                    problem = None
                except BridgeError as exc:
                    models, connected, problem = [], False, exc.code
                return {"connected": connected, "error": problem, "available_models": len(models),
                        "local_usage": self.store.stats(), "hourly_limit": self.config.max_calls_per_hour}
            if tool == "chatgpt_session_create":
                mode = args.get("mode", "general")
                if mode not in PRESETS:
                    raise BridgeError("invalid session mode", "invalid_mode")
                return self.store.new_session(mode, str(args.get("label", "Untitled")))
            if tool == "chatgpt_sessions":
                return {"sessions": self.store.list_sessions(int(args.get("limit", 30)))}
            if tool == "chatgpt_history":
                sid = args["session_id"]
                if not self.store.session(sid):
                    raise BridgeError("session_id not found", "session_not_found", 404)
                return {"session_id": sid, "messages": self.store.history(sid, min(100, int(args.get("limit", 24))))}
            if tool == "chatgpt_session_delete":
                return {"deleted": self.store.delete_session(args["session_id"])}
        except KeyError as exc:
            raise BridgeError(f"Missing required argument: {exc.args[0]}", "invalid_input") from exc
        except (TypeError, ValueError) as exc:
            raise BridgeError(str(exc), "invalid_input") from exc
        raise BridgeError(f"Unknown tool: {tool}", "unknown_tool", 404)
