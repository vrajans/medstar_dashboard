"""
ai/llm_gateway.py  —  InsightHub LLM Gateway
============================================
A single, provider-agnostic interface for all LLM calls, with:

  • Pluggable backends   — Groq (default), OpenAI, Anthropic, Azure OpenAI,
                            DeepSeek, OpenRouter (any OpenAI-compatible endpoint).
  • Per-tenant BYO keys  — a tenant can bring its own provider + API key + model
                            (frontier quality or its own compliance boundary),
                            resolved automatically via request context.
  • Dependency-light     — pure `urllib` (no vendor SDKs), matching the existing
                            groq_client transport.

Design
------
  resolve_config(tenant_id) → LLMConfig     (tenant BYO → else platform default)
  chat(messages, ...)       → str | None    (never raises; returns None on failure)
  embed(texts, ...)         → list[list[float]] | None

Platform default is controlled by env:
  LLM_PROVIDER (default "groq"), LLM_MODEL (optional override),
  and the provider's own key env var (e.g. GROQ_API_KEY, OPENAI_API_KEY).

Per-tenant BYO config is stored in `tenant_llm_config` (see init_llm_config_table).
NOTE: BYO API keys are secrets — in production store them in a managed vault /
encrypt at rest. This table is the integration point for that.
"""

from __future__ import annotations

import os
import json
import logging
import urllib.request
import urllib.error
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# provider registry
# ─────────────────────────────────────────────────────────────────────────────
PROVIDERS: dict[str, dict] = {
    "groq": {
        "base": "https://api.groq.com/openai/v1", "style": "openai",
        "default_model": "llama-3.3-70b-versatile", "env": "GROQ_API_KEY",
        "label": "Groq (Llama 3.3, free tier)",
    },
    "openai": {
        "base": "https://api.openai.com/v1", "style": "openai",
        "default_model": "gpt-4o-mini", "env": "OPENAI_API_KEY",
        "label": "OpenAI",
    },
    "anthropic": {
        "base": "https://api.anthropic.com/v1", "style": "anthropic",
        "default_model": "claude-3-5-sonnet-20241022", "env": "ANTHROPIC_API_KEY",
        "label": "Anthropic (Claude)",
    },
    "azure": {
        "base": None, "style": "azure",          # base_url is per-tenant
        "default_model": None, "env": "AZURE_OPENAI_API_KEY",
        "label": "Azure OpenAI",
    },
    "deepseek": {
        "base": "https://api.deepseek.com/v1", "style": "openai",
        "default_model": "deepseek-chat", "env": "DEEPSEEK_API_KEY",
        "label": "DeepSeek",
    },
    "openrouter": {
        "base": "https://openrouter.ai/api/v1", "style": "openai",
        "default_model": "meta-llama/llama-3.3-70b-instruct", "env": "OPENROUTER_API_KEY",
        "label": "OpenRouter",
    },
}

USER_AGENT = "InsightHub/1.0 (LLM Gateway)"


@dataclass
class LLMConfig:
    provider:    str
    model:       str
    api_key:     str
    base_url:    Optional[str] = None
    style:       str = "openai"
    api_version: str = "2024-02-15-preview"   # azure only
    extra:       dict = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return bool(self.api_key) and bool(self.model)


# ─────────────────────────────────────────────────────────────────────────────
# request-scoped tenant context (so BYO routing needs no param threading)
# ─────────────────────────────────────────────────────────────────────────────
_ctx: ContextVar[dict] = ContextVar("llm_ctx", default={})

def set_tenant_context(tenant_id: Optional[int], engine=None) -> None:
    """Call at the start of a request/callback so chat() routes to the tenant's BYO LLM."""
    _ctx.set({"tenant_id": tenant_id, "engine": engine})

def clear_tenant_context() -> None:
    _ctx.set({})


# ─────────────────────────────────────────────────────────────────────────────
# config resolution
# ─────────────────────────────────────────────────────────────────────────────
def resolve_config(tenant_id: Optional[int] = None, engine=None) -> LLMConfig:
    """Resolve the LLM config: tenant BYO if configured, else the platform default."""
    ctx = _ctx.get()
    if tenant_id is None:
        tenant_id = ctx.get("tenant_id")
    if engine is None:
        engine = ctx.get("engine")

    # 1) tenant bring-your-own
    if tenant_id is not None and engine is not None:
        try:
            byo = get_tenant_llm(engine, int(tenant_id))
            if byo and byo.get("api_key"):
                prov = PROVIDERS.get(byo["provider"], {})
                return LLMConfig(
                    provider=byo["provider"],
                    model=byo.get("model") or prov.get("default_model") or "",
                    api_key=byo["api_key"],
                    base_url=byo.get("base_url") or prov.get("base"),
                    style=prov.get("style", "openai"),
                    api_version=byo.get("api_version") or "2024-02-15-preview",
                )
        except Exception as e:
            logger.debug("[llm] tenant config lookup failed: %s", e)

    # 2) platform default (env-driven)
    provider = os.getenv("LLM_PROVIDER", "groq")
    prov = PROVIDERS.get(provider, PROVIDERS["groq"])
    return LLMConfig(
        provider=provider,
        model=os.getenv("LLM_MODEL") or prov.get("default_model") or "",
        api_key=os.getenv(prov.get("env", "GROQ_API_KEY"), ""),
        base_url=os.getenv("LLM_BASE_URL") or prov.get("base"),
        style=prov.get("style", "openai"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# transport
# ─────────────────────────────────────────────────────────────────────────────
def _post(url: str, headers: dict, payload: dict, timeout: int = 30) -> Optional[dict]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"User-Agent": USER_AGENT, **headers},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        logger.error("[llm] HTTP %s — %s", exc.code, body[:300])
    except Exception as exc:
        logger.error("[llm] request failed: %s", exc)
    return None


def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """Anthropic wants the system prompt separate from the message list."""
    system = "\n".join(m["content"] for m in messages if m.get("role") == "system")
    rest = [m for m in messages if m.get("role") != "system"]
    return system, rest


# ─────────────────────────────────────────────────────────────────────────────
# public: chat
# ─────────────────────────────────────────────────────────────────────────────
def chat(messages: list[dict], *, tenant_id: Optional[int] = None, engine=None,
         max_tokens: int = 512, temperature: float = 0.3,
         model: Optional[str] = None, config: Optional[LLMConfig] = None) -> Optional[str]:
    """
    Provider-agnostic chat completion. Returns assistant text, or None on failure.
    Routing precedence: explicit config → explicit tenant_id → request context → platform default.
    """
    cfg = config or resolve_config(tenant_id, engine)
    if model:
        cfg.model = model
    if not cfg.usable:
        logger.warning("[llm] no usable config (provider=%s, model=%s, key set=%s)",
                       cfg.provider, cfg.model, bool(cfg.api_key))
        return None

    if cfg.style == "anthropic":
        system, msgs = _split_system(messages)
        payload = {"model": cfg.model, "max_tokens": max_tokens,
                   "temperature": temperature, "messages": msgs}
        if system:
            payload["system"] = system
        res = _post(f"{cfg.base_url}/messages",
                    {"x-api-key": cfg.api_key, "anthropic-version": "2023-06-01",
                     "Content-Type": "application/json"}, payload)
        if res:
            try:
                return "".join(b.get("text", "") for b in res.get("content", []))
            except Exception:
                return None
        return None

    if cfg.style == "azure":
        url = f"{cfg.base_url}/openai/deployments/{cfg.model}/chat/completions?api-version={cfg.api_version}"
        res = _post(url, {"api-key": cfg.api_key, "Content-Type": "application/json"},
                    {"messages": messages, "max_tokens": max_tokens, "temperature": temperature})
    else:  # openai-compatible (groq, openai, deepseek, openrouter, …)
        res = _post(f"{cfg.base_url}/chat/completions",
                    {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"},
                    {"model": cfg.model, "messages": messages,
                     "max_tokens": max_tokens, "temperature": temperature, "stream": False})
    if res:
        try:
            return res["choices"][0]["message"]["content"]
        except Exception:
            logger.error("[llm] unexpected response shape: %s", str(res)[:200])
    return None


# ─────────────────────────────────────────────────────────────────────────────
# public: embed (OpenAI-compatible providers; optional)
# ─────────────────────────────────────────────────────────────────────────────
def embed(texts: list[str], *, tenant_id: Optional[int] = None, engine=None,
          model: Optional[str] = None, config: Optional[LLMConfig] = None) -> Optional[list[list[float]]]:
    cfg = config or resolve_config(tenant_id, engine)
    if cfg.style not in ("openai", "azure") or not cfg.api_key:
        return None
    emb_model = model or ("text-embedding-3-small" if cfg.provider == "openai" else cfg.model)
    if cfg.style == "azure":
        url = f"{cfg.base_url}/openai/deployments/{emb_model}/embeddings?api-version={cfg.api_version}"
        hdr = {"api-key": cfg.api_key, "Content-Type": "application/json"}
        res = _post(url, hdr, {"input": texts})
    else:
        res = _post(f"{cfg.base_url}/embeddings",
                    {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"},
                    {"model": emb_model, "input": texts})
    if res:
        try:
            return [d["embedding"] for d in res["data"]]
        except Exception:
            return None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# per-tenant BYO config storage
# ─────────────────────────────────────────────────────────────────────────────
def init_llm_config_table(engine) -> None:
    from sqlalchemy import text
    with engine.begin() as c:
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS tenant_llm_config (
                tenant_id   INTEGER PRIMARY KEY,
                provider    TEXT,
                model       TEXT,
                api_key     TEXT,
                base_url    TEXT,
                api_version TEXT,
                updated_at  TEXT
            )
        """))


def set_tenant_llm(engine, tenant_id: int, provider: str, api_key: str,
                   model: Optional[str] = None, base_url: Optional[str] = None,
                   api_version: Optional[str] = None) -> None:
    from sqlalchemy import text
    from datetime import datetime
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider '{provider}'. Options: {', '.join(PROVIDERS)}")
    init_llm_config_table(engine)
    with engine.begin() as c:
        c.execute(text("DELETE FROM tenant_llm_config WHERE tenant_id=:t"), {"t": tenant_id})
        c.execute(text("""
            INSERT INTO tenant_llm_config
                (tenant_id, provider, model, api_key, base_url, api_version, updated_at)
            VALUES (:t, :p, :m, :k, :b, :v, :now)
        """), {"t": tenant_id, "p": provider, "m": model, "k": api_key,
               "b": base_url, "v": api_version,
               "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})


def get_tenant_llm(engine, tenant_id: int) -> Optional[dict]:
    from sqlalchemy import text
    try:
        with engine.connect() as c:
            row = c.execute(text(
                "SELECT provider, model, api_key, base_url, api_version "
                "FROM tenant_llm_config WHERE tenant_id=:t"), {"t": tenant_id}).fetchone()
        if not row:
            return None
        return {"provider": row[0], "model": row[1], "api_key": row[2],
                "base_url": row[3], "api_version": row[4]}
    except Exception:
        return None


def clear_tenant_llm(engine, tenant_id: int) -> None:
    from sqlalchemy import text
    try:
        with engine.begin() as c:
            c.execute(text("DELETE FROM tenant_llm_config WHERE tenant_id=:t"), {"t": tenant_id})
    except Exception:
        pass


def list_providers() -> dict:
    """Provider catalogue for a settings UI (no secrets)."""
    return {k: {"label": v["label"], "default_model": v["default_model"], "style": v["style"]}
            for k, v in PROVIDERS.items()}


def health(tenant_id: Optional[int] = None, engine=None) -> dict:
    """Resolve config and make a tiny live call to confirm the provider works."""
    cfg = resolve_config(tenant_id, engine)
    out = {"provider": cfg.provider, "model": cfg.model, "key_set": bool(cfg.api_key)}
    if not cfg.usable:
        out["ok"] = False; out["detail"] = "no api key / model"
        return out
    reply = chat([{"role": "user", "content": "Reply with the single word: ok"}],
                 config=cfg, max_tokens=5, temperature=0)
    out["ok"] = reply is not None
    out["detail"] = (reply or "no response")[:60]
    return out
