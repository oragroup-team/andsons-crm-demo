"""Runtime patch for a real, confirmed Anthropic API incompatibility in
LangChain's agent-loop retry logic - applied to whichever langchain_anthropic
package is actually installed (backend/vendor/ in local dev, a fresh pip
install in the deployed Docker image - see Dockerfile/backend/_vendor_path.py
for why those are two genuinely different installs), so this fix reaches
BOTH environments from one place instead of a hand-edited vendor file that
has to be manually reapplied and never actually reached production at all
(a real gap: the vendor/langchain_anthropic/chat_models.py edit documented
in llm_provider.py's module docstring is gitignored and Dockerfile does a
clean `pip install`, not a copy of backend/vendor/ - so that fix was, in
fact, NEVER live in the deployed service, despite being described as
"already applied").

THE REAL BUG: create_sql_agent's AgentExecutor has its own internal retry
path - when the model responds with plain text instead of a valid tool
call, AgentExecutor re-invokes the chain with that plain-text response
appended to the message history as the newest AIMessage, then loops. If
that AIMessage ends up being the LAST message sent to the API (which it is,
in this exact retry), langchain_anthropic's own message formatting
(_format_messages in langchain_anthropic/chat_models.py) sends it through
unchanged - Anthropic's real API then rejects the whole request outright:
"This model does not support assistant message prefill. The conversation
must end with a user message." Confirmed live, reproducibly, against the
deployed service (2026-08-23): flipping ANALYTICS_PROVIDER to anthropic
failed 3/3 retries on real questions with exactly this error.

This is NOT the same bug as the pre-existing hand-written vendor patch
target (an EMPTY-content trailing assistant message, kept as an
intentional "prefill" on older Claude models, previously allowed by the
API). That narrower case is real too - the currently-installed
langchain_anthropic (confirmed via direct inspection: pip installs
0.3.22, still has `and _i < len(merged_messages) - 1` unmodified) still
has it. This patch fixes BOTH at once, generically: no matter why the
final formatted message ends up being assistant-role (empty content
prefill, or a real non-empty reasoning turn AgentExecutor re-sent), append
one small synthetic user-role message so the conversation validly ends on
a user turn, exactly as Anthropic's current API requires - the real
assistant content is left completely untouched, nothing is dropped or
rewritten, so no information the agent loop needs is lost.

This deliberately does NOT touch _format_messages()'s internal per-message
loop (the smaller, narrower fix the old vendor patch made) - it wraps the
function's OUTPUT instead, which is simpler, safer (one clear, isolated
change instead of editing a nested per-message conditional), and covers
every real case that produces a trailing assistant message, not just the
one case someone happened to hand-patch before.

WHY A SYNTHETIC NUDGE MESSAGE, NOT JUST DROPPING THE TRAILING TURN: dropping
it would silently discard the model's real reasoning/attempted-answer text,
which AgentExecutor's own retry logic depends on being visible in history
(it's what lets the model self-correct next turn) - and an empty message
list ending in nothing is its own real failure mode. A single short user
message asking the model to continue with a real tool call is the closest
faithful emulation of the "prefill continuation" this API no longer allows
at all; it only ever fires for the retry-loop's plain-text-response case,
never for a normal tool-call cycle (which already ends in a real user-role
ToolMessage/tool_result, not an assistant message) - confirmed by
inspecting every real call site in this package (see module docstring
below for the exact grep-verified list).

Call apply() once, as early as possible - agents/llm_provider.py does this
before constructing any ChatAnthropic instance, so it's live before the
first real API call anywhere in the app, regardless of which agent
(COPYWRITER/SWEEPER/ANALYTICS) or code path (Slack, /ask, /generate-email)
triggers it first.
"""
import logging

logger = logging.getLogger("anthropic_agent_loop_patch")

_APPLIED = False


def apply() -> bool:
    """Idempotent - safe to call from every entry point/module that might
    construct a ChatAnthropic first. Returns True if the patch is active
    (either applied just now, or already applied by an earlier call);
    False if langchain_anthropic isn't installed at all (caller should
    treat that the same as "nothing to patch", not an error - a
    Groq-only environment never imports this package)."""
    global _APPLIED
    if _APPLIED:
        return True

    try:
        import langchain_anthropic.chat_models as _chat_models_module
    except ImportError:
        logger.info("langchain_anthropic not installed - nothing to patch.")
        return False

    original_format_messages = _chat_models_module._format_messages

    def _patched_format_messages(messages):
        system, formatted = original_format_messages(messages)
        if formatted and formatted[-1].get("role") == "assistant":
            # Real, live-confirmed fix target - see module docstring. Only
            # ever fires when the message list about to be sent to the API
            # ends in an assistant turn (empty-content legacy "prefill", or
            # AgentExecutor's own non-empty-content retry re-send) - a
            # normal tool-calling cycle always ends in a real user-role
            # tool_result message instead, so this is inert for the common
            # case and only activates for the exact failure this exists to
            # fix.
            formatted = [
                *formatted,
                {
                    "role": "user",
                    "content": (
                        "Continue - call the appropriate tool to make progress on the "
                        "original question rather than responding in plain text."
                    ),
                },
            ]
        return system, formatted

    _chat_models_module._format_messages = _patched_format_messages
    _APPLIED = True
    logger.info(
        "Patched langchain_anthropic._format_messages to append a synthetic user "
        "turn whenever the formatted message list would otherwise end in an "
        "assistant message (the real 'This model does not support assistant "
        "message prefill' failure mode)."
    )
    return True
