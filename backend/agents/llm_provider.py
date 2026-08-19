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

REQUIRED VENDORED PATCH - not tracked by git, must be reapplied if
backend/vendor/ is ever regenerated/reinstalled: vendor/langchain_anthropic/
chat_models.py's _format_messages() has a real, confirmed bug for the
current Claude model family - it deliberately keeps a trailing EMPTY-content
assistant message as an intentional "prefill" (a real, legitimate technique
on older Claude models), but the current API (Opus 5, Sonnet 5, and the
4.6+ family) rejects that outright: "This model does not support assistant
message prefill. The conversation must end with a user message." Find the
line `if not content and role == "assistant" and _i < len(merged_messages) - 1:`
and remove the `and _i < len(merged_messages) - 1` condition so an empty
assistant message is always dropped, not just when it isn't last. (backend/
vendor/ is gitignored - a large third-party bundle, not meant to be
committed - so this fix has to be reapplied by hand if that directory is
ever refreshed from a clean pip install; there is no other record of it.)
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
    "HEAD_OF_CRM": "groq",
    "COPYWRITER": "groq",
    "CREATIVE_DIRECTOR": "groq",
    # Real constraint, not a preference: Groq has no vision-capable model
    # in this account (confirmed live - a direct probe call rejects any
    # multi-part/image message content outright), so Visual QA can only
    # ever run for real on Anthropic. visual_qa_agent.py checks
    # anthropic_available() itself before attempting a call, and skips
    # cleanly (never a hard failure) rather than sending an image to a
    # model that will just reject the request shape.
    "VISUAL_QA": "anthropic",
    "SWEEPER": "anthropic",
    # NOT anthropic, despite this looking like the obvious choice: the
    # LangChain SQL ReAct agent (create_sql_agent) this agent runs on has a
    # real, confirmed incompatibility with the current Claude API - caught
    # live, reproducibly, across every question tried. When the model
    # writes a plain-text reasoning turn mid-loop instead of a tool call,
    # AgentExecutor's own retry path re-sends that text as the start of
    # the NEXT turn (a continuation "prefill") - a pattern the current
    # Claude model family rejects outright ("This model does not support
    # assistant message prefill"). This isn't the smaller max_tokens
    # truncation issue already fixed above (verified: still fails
    # identically at max_tokens=16000) - it's AgentExecutor's own
    # scratchpad-continuation logic, which would need a real LangChain-
    # internals fix, not a config change, to run on Anthropic. Groq has
    # been reliable for this exact SQL-agent flow all session - stay on
    # it here rather than ship a "live data" feature that silently fails
    # every time.
    "ANALYTICS": "groq",
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

        # No temperature kwarg here - see get_llm()'s Anthropic branch for why.
        probe = ChatAnthropic(model=DEFAULT_MODELS["anthropic"], api_key=api_key, max_tokens=4)
        probe.invoke("Hi")
        _anthropic_health.update(ok=True, checked_at=now)
        return True
    except Exception as exc:  # noqa: BLE001 - any failure (auth, billing, network) means "fall back"
        logger.warning("Anthropic unavailable (%s) - falling back to Groq until the next recheck.", exc)
        _anthropic_health.update(ok=False, checked_at=now)
        return False


def anthropic_available() -> bool:
    """Public wrapper - lets a caller (visual_qa_agent.py) check ahead of
    time whether a vision-capable model is actually reachable right now,
    since Groq has no vision model in this account at all (confirmed live)
    and get_llm()'s own fallback would otherwise silently hand back a
    text-only model for a call that needs to send an image."""
    return _anthropic_available()


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
        # Real, verified API change (confirmed live, direct probe against
        # the real Anthropic API): the current Claude model family rejects
        # any explicit temperature value other than 1 outright ("`temperature`
        # is deprecated for this model") - passing our usual 0.0 (deterministic
        # calls) or 0.8 (creative calls) both hard-fail every request. There
        # is no way to tune this anymore for these models - the caller's
        # `temperature` argument is accepted for API-shape compatibility with
        # the Groq branch above but deliberately NOT forwarded here.
        #
        # max_tokens: langchain_anthropic's own default is a real, verified
        # problem, not a safe default - only 1024. claude-opus-5 runs
        # adaptive thinking ON BY DEFAULT (confirmed), which spends real
        # output-token budget from that same 1024 cap before the model even
        # gets to its actual answer/tool call - caught live, repeatedly:
        # the SQL agent's response kept getting cut off mid-generation
        # ("received a `max_tokens` stop reason"), and LangChain's recovery
        # for that (re-sending the truncated partial response as a
        # continuation prefill) is itself rejected by the current API
        # ("This model does not support assistant message prefill") - so a
        # too-small max_tokens was silently turning into a hard failure
        # two layers downstream instead of a clean truncation. Set high
        # enough that thinking + a real structured/tool-calling response
        # both fit comfortably.
        return ChatAnthropic(model=model, api_key=api_key, max_tokens=16000)

    raise ValueError(f"Unknown provider '{provider}' for agent {agent_key}. Use 'groq' or 'anthropic'.")


def invoke_with_retry(chain, attempts: int = 5, backoff_seconds: float = 1.5, label: str = "LLM call"):
    """Shared retry wrapper for a structured-output chain.invoke({}) call -
    used by every Copywriter/Sweeper call site. Two distinct real,
    repeatedly-observed failure modes this covers:
    1. Groq occasionally returns a transient 404 'model does not exist' for
       a model that demonstrably works seconds before and after (confirmed
       live - not an actual deprecation), which reads like a load-balancer
       routing a request to an unready shard.
    2. Groq's forced-tool-call mode ("Tool choice is required, but model
       did not call a tool") - the model answers in plain conversational
       text instead of the required structured format. Caught live on a
       real WhatsApp touchpoint generation: 3 attempts in a row hit this
       and exhausted the retry budget, surfacing as a hard failure to the
       Slack user. Output is non-deterministic enough between attempts
       (confirmed: 3 different plain-text answers across 3 failed
       attempts) that more attempts meaningfully raises the odds of one
       landing as a real tool call.
    A flow's multiple back-to-back Copywriter+Sweeper calls (up to ~25 for
    a 5-touchpoint flow with corrections) hit both of these far more than a
    single email ever did, so more headroom than a plain single-email
    retry needs is warranted here. Returns the result, or (None,
    last_exception) if every attempt fails - the caller decides how to
    fail (raise vs fail-closed)."""
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
