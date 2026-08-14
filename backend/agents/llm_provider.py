"""Per-agent LLM provider factory.

Each agent (COPYWRITER, SWEEPER, ANALYTICS) picks its provider and model via
environment variables so the demo can swap providers without touching code:

  COPYWRITER_PROVIDER=groq|anthropic
  SWEEPER_PROVIDER=groq|anthropic
  ANALYTICS_PROVIDER=groq|anthropic

  <AGENT>_GROQ_MODEL / <AGENT>_ANTHROPIC_MODEL  (optional overrides)

Automatic Anthropic -> Groq fallback: while ANTHROPIC_API_KEY exists but the
account has no usable credit (or any other call-time failure), every agent
configured for "anthropic" transparently falls back to Groq instead of
hard-failing every request. This is deliberately NOT implemented as
`llm.with_fallbacks([...])` - that returns a generic RunnableWithFallbacks,
which lacks the chat-model-specific methods every call site here actually
uses (`.with_structured_output()` in copywriter_agent.py/sweeper_agent.py,
tool-calling/`.bind_tools()` inside create_sql_agent in analytics_agent.py) -
wrapping at that level would break every one of them. Instead, get_llm()
does a cheap real health probe up front and returns a genuine ChatAnthropic
or ChatGroq instance - whichever actually works - so every downstream
`.with_structured_output()` / create_sql_agent call sees a normal, fully
capable chat model either way.

The health check result is cached for _HEALTH_CHECK_TTL so a broken
Anthropic key doesn't cost a failed API call on every single request, but
still re-checks periodically - once credits are topped up, the app starts
using Claude again on its own, without a restart or redeploy.
"""
import logging
import os
import time

logger = logging.getLogger("llm_provider")

DEFAULT_MODELS = {
    "groq": "llama-3.3-70b-versatile",
    "anthropic": "claude-opus-5",
}

DEFAULT_PROVIDERS = {
    "COPYWRITER": "groq",
    "SWEEPER": "anthropic",
    "ANALYTICS": "anthropic",
}

_HEALTH_CHECK_TTL = 300  # 5 min - see module docstring
_anthropic_health = {"ok": None, "checked_at": 0.0}


def _anthropic_available() -> bool:
    """One real, cheap call to confirm Anthropic is actually usable right
    now (auth + billing, not just "is a key set") - cached so this costs at
    most one extra API call per _HEALTH_CHECK_TTL window, shared across all
    three agents, not once per agent per request."""
    now = time.time()
    if _anthropic_health["ok"] is not None and (now - _anthropic_health["checked_at"]) < _HEALTH_CHECK_TTL:
        return _anthropic_health["ok"]

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        _anthropic_health.update(ok=False, checked_at=now)
        return False

    try:
        from langchain_anthropic import ChatAnthropic

        probe = ChatAnthropic(model=DEFAULT_MODELS["anthropic"], api_key=api_key, temperature=0, max_tokens=4)
        probe.invoke("Hi")
        _anthropic_health.update(ok=True, checked_at=now)
        return True
    except Exception as exc:  # noqa: BLE001 - any failure (auth, billing, network) means "fall back"
        logger.warning("Anthropic unavailable (%s) - falling back to Groq until the next recheck.", exc)
        _anthropic_health.update(ok=False, checked_at=now)
        return False


def get_llm(agent_name: str, temperature: float = 0.0):
    """Build a chat model for the given agent based on env-configured provider.

    agent_name: one of "COPYWRITER", "SWEEPER", "ANALYTICS"
    """
    agent_key = agent_name.upper()
    provider = os.environ.get(
        f"{agent_key}_PROVIDER", DEFAULT_PROVIDERS.get(agent_key, "anthropic")
    ).lower()

    if provider == "anthropic" and not _anthropic_available():
        provider = "groq"

    model = os.environ.get(f"{agent_key}_{provider.upper()}_MODEL", DEFAULT_MODELS[provider])

    if provider == "groq":
        from langchain_groq import ChatGroq

        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError(
                f"{agent_key}_PROVIDER=groq but GROQ_API_KEY is not set in the environment."
            )
        return ChatGroq(model=model, temperature=temperature, api_key=api_key)

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                f"{agent_key}_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set in the environment."
            )
        return ChatAnthropic(model=model, temperature=temperature, api_key=api_key)

    raise ValueError(f"Unknown provider '{provider}' for agent {agent_key}. Use 'groq' or 'anthropic'.")
