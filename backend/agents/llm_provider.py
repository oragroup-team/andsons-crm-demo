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
    # llama-3.3-70b-versatile (the previous default) is now genuinely
    # deprecated on Groq - confirmed directly against the Groq API (a raw
    # chat.completions.create call 404s every time, not just occasionally),
    # not the transient routing issue this codebase otherwise retries
    # around. openai/gpt-oss-120b is the model the deployed Cloud Run
    # service has actually been running on (via COPYWRITER_GROQ_MODEL etc.
    # in cloudrun-env.yaml) - confirmed working - so it's the safer default
    # for any environment that doesn't set an explicit override.
    "groq": "openai/gpt-oss-120b",
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


def invoke_with_retry(chain, attempts: int = 3, backoff_seconds: float = 1.5, label: str = "LLM call"):
    """Shared retry wrapper for a structured-output chain.invoke({}) call -
    used by every Copywriter/Sweeper call site. Real, repeatedly-observed
    failure mode this covers: Groq occasionally returns a transient 404
    'model does not exist' for a model that demonstrably works seconds
    before and after (confirmed live - not an actual deprecation), which
    reads like a load-balancer routing a request to an unready shard. A
    flow's multiple back-to-back Copywriter+Sweeper calls (up to ~15 for a
    5-touchpoint flow with retries) hit this far more than a single email
    ever did, so 2 attempts with no pause was no longer enough headroom -
    3 attempts with a short backoff between them gives a bad route a moment
    to clear instead of hitting it again immediately. Returns the result,
    or (None, last_exception) if every attempt fails - the caller decides
    how to fail (raise vs fail-closed)."""
    last_exc = None
    for attempt in range(attempts):
        try:
            return chain.invoke({}), None
        except Exception as exc:  # noqa: BLE001 - every attempt logged, caller decides final handling
            last_exc = exc
            logger.warning("%s failed (attempt %d/%d): %s", label, attempt + 1, attempts, exc)
            if attempt < attempts - 1:
                time.sleep(backoff_seconds)
    return None, last_exc
