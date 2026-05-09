"""AI client — sends assembled prompts to the configured provider and returns scored dict."""
from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

from ...config import AiCfg

# ---------------------------------------------------------------------------
# Provider defaults
# ---------------------------------------------------------------------------

_DEFAULT_BASE_URLS: dict[str, str] = {
    "openai":        "https://api.openai.com/v1",
    "ollama":        "http://localhost:11434/v1",
    "lmstudio":      "http://localhost:1234/v1",
    "grok":          "https://api.x.ai/v1",
    "gemini":        "https://generativelanguage.googleapis.com/v1beta/openai",
    "deepseek":      "https://api.deepseek.com",
    "anthropic":     "https://api.anthropic.com",
    "openai_compat": "",  # must be supplied by user
}

_DEFAULT_MODELS: dict[str, str] = {
    "openai":        "gpt-4o-mini",
    "ollama":        "llama3.2",
    "lmstudio":      "llama-3.2-3b-instruct",
    "grok":          "grok-4-1-fast-non-reasoning",
    "gemini":        "gemini-2.0-flash",
    "deepseek":      "deepseek-v4-flash",
    "anthropic":     "claude-sonnet-4-5",
    "openai_compat": "",
}

# Providers that need no auth (empty key is fine)
_NO_AUTH_PROVIDERS = {"ollama", "lmstudio"}

# Providers that use a non-standard key name for max output tokens
_MAX_TOKENS_KEY: dict[str, str] = {
    "grok": "max_completion_tokens",
}

# Supported parameters per provider — single source of truth for both
# payload building and the UI. Add a new param here and it flows everywhere.
PROVIDER_PARAMS: dict[str, tuple[str, ...]] = {
    "openai":        ("temperature", "max_tokens", "top_p", "frequency_penalty", "presence_penalty", "seed", "reasoning_effort"),
    "grok":          ("temperature", "max_tokens", "top_p", "frequency_penalty", "presence_penalty", "seed"),
    "gemini":        ("temperature", "max_tokens", "top_p"),
    "deepseek":      ("temperature", "max_tokens", "top_p", "reasoning_effort", "thinking_enabled"),
    "anthropic":     ("temperature", "max_tokens", "top_p"),
    "ollama":        ("temperature", "max_tokens", "top_p"),
    "lmstudio":      ("temperature", "max_tokens", "top_p"),
    "openai_compat": ("temperature", "max_tokens", "top_p", "frequency_penalty", "presence_penalty", "seed", "reasoning_effort", "thinking_enabled"),
}

# Timeout for AI calls — models can be slow
_TIMEOUT = 120


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_job(cfg: AiCfg, system: str, user: str) -> dict[str, Any]:
    """Send system + user prompt to the configured provider and return parsed JSON dict.

    Raises ValueError if the response cannot be parsed as valid JSON.
    Raises httpx.HTTPStatusError / httpx.TimeoutException on network failures.
    """
    if cfg.provider == "anthropic":
        return _score_anthropic(cfg, system, user)
    return _score_openai_compat(cfg, system, user)


def _resolve_params(cfg: AiCfg) -> dict[str, Any]:
    """Return effective params for the current provider.

    Per-provider provider_params[provider] is the primary source. Falls back to
    AiCfg top-level fields (temperature, max_tokens, reasoning_effort, thinking_enabled),
    all of which default to None — meaning the param is omitted from the API payload
    unless explicitly saved. Supported keys: temperature, max_tokens, top_p,
    frequency_penalty, presence_penalty, seed, reasoning_effort, thinking_enabled.
    """
    # Structural defaults from AiCfg (only used when provider has no saved params)
    defaults: dict[str, Any] = {
        "temperature":       cfg.temperature,
        "max_tokens":        cfg.max_tokens,
        "top_p":             None,
        "frequency_penalty": None,
        "presence_penalty":  None,
        "seed":              None,
        "reasoning_effort":  cfg.reasoning_effort,
        "thinking_enabled":  cfg.thinking_enabled,
    }
    # Per-provider saved params take precedence; ignore empty/None values
    saved = cfg.provider_params.get(cfg.provider, {})
    result = dict(defaults)
    for k, v in saved.items():
        if k in result and v is not None and v != "":
            result[k] = v
    return result


def _score_openai_compat(cfg: AiCfg, system: str, user: str) -> dict[str, Any]:
    """OpenAI-compatible /chat/completions endpoint."""
    base_url = (cfg.base_url or "").rstrip("/") or _DEFAULT_BASE_URLS.get(cfg.provider, "")
    if not base_url:
        raise ValueError(
            f"Provider '{cfg.provider}' requires a base_url — set it in AI settings."
        )

    model = cfg.model or _DEFAULT_MODELS.get(cfg.provider, "")
    if not model:
        raise ValueError(
            f"Provider '{cfg.provider}' requires a model name — set it in AI settings."
        )

    api_key = (os.environ.get("AI_API_KEY")
               or cfg.api_keys.get(cfg.provider, "")
               or cfg.api_key)
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    elif cfg.provider not in _NO_AUTH_PROVIDERS:
        raise ValueError(
            f"Provider '{cfg.provider}' requires an API key — "
            "set AI_API_KEY env var or api_key in AI settings."
        )

    params = _resolve_params(cfg)
    supported = set(PROVIDER_PARAMS.get(cfg.provider, params.keys()))

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }
    # Standard params — same key name in the API
    for key in ("temperature", "top_p", "frequency_penalty", "presence_penalty", "seed", "reasoning_effort"):
        if key in supported and params[key] is not None:
            payload[key] = params[key]
    # max_tokens — key name varies by provider
    if "max_tokens" in supported and params["max_tokens"] is not None:
        payload[_MAX_TOKENS_KEY.get(cfg.provider, "max_tokens")] = params["max_tokens"]
    # thinking — special structure; must be explicit for providers that default to thinking-on
    if "thinking_enabled" in supported:
        if params["thinking_enabled"] is True:
            payload["thinking"] = {"type": "enabled"}
        elif params["thinking_enabled"] is False:
            payload["thinking"] = {"type": "disabled"}

    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()

    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    return _parse_json_response(content)


def _score_anthropic(cfg: AiCfg, system: str, user: str) -> dict[str, Any]:
    """Anthropic /v1/messages endpoint."""
    model = cfg.model or _DEFAULT_MODELS["anthropic"]

    api_key = (os.environ.get("AI_API_KEY")
               or cfg.api_keys.get("anthropic", "")
               or cfg.api_key)
    if not api_key:
        raise ValueError(
            "Anthropic requires an API key — set AI_API_KEY env var or api_key in AI settings."
        )

    base_url = (cfg.base_url or "").rstrip("/") or "https://api.anthropic.com"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    params = _resolve_params(cfg)
    supported = set(PROVIDER_PARAMS.get("anthropic", ()))
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": params["max_tokens"] or 8192,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    for key in ("temperature", "top_p"):
        if key in supported and params[key] is not None:
            payload[key] = params[key]

    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(f"{base_url}/v1/messages", headers=headers, json=payload)
        resp.raise_for_status()

    data = resp.json()
    content = data["content"][0]["text"]
    return _parse_json_response(content)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _parse_json_response(content: str) -> dict[str, Any]:
    """Strip markdown fences and parse JSON from model output."""
    text = content.strip()
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Model did not return valid JSON. Error: {exc}. "
            f"Raw response (first 300 chars): {content[:300]!r}"
        ) from exc
    if not isinstance(result, dict):
        raise ValueError(
            f"Expected a JSON object, got {type(result).__name__}. "
            f"Raw: {content[:300]!r}"
        )
    return result
