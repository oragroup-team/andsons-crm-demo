"""Per-agent LLM provider factory.

Each agent (COPYWRITER, SWEEPER, ANALYTICS) picks its provider and model via
environment variables so the demo can swap providers without touching code:

  COPYWRITER_PROVIDER=groq|anthropic
  SWEEPER_PROVIDER=groq|anthropic
  ANALYTICS_PROVIDER=groq|anthropic

  <AGENT>_GROQ_MODEL / <AGENT>_ANTHROPIC_MODEL  (optional overrides)
"""
import os

DEFAULT_MODELS = {
    "groq": "llama-3.3-70b-versatile",
    "anthropic": "claude-opus-5",
}

DEFAULT_PROVIDERS = {
    "COPYWRITER": "groq",
    "SWEEPER": "anthropic",
    "ANALYTICS": "anthropic",
}


def get_llm(agent_name: str, temperature: float = 0.0):
    """Build a chat model for the given agent based on env-configured provider.

    agent_name: one of "COPYWRITER", "SWEEPER", "ANALYTICS"
    """
    agent_key = agent_name.upper()
    provider = os.environ.get(
        f"{agent_key}_PROVIDER", DEFAULT_PROVIDERS.get(agent_key, "anthropic")
    ).lower()

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
