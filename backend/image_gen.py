"""Hero image generation - used only when the Copywriter picks hero:"generate"
because no approved bank photo genuinely fits the email (see
backend/image_bank.py). The real andSons production system uses OpenAI
gpt-image-1 for this, capped at one generated image per flow; we mirror
that here, capped at one generation per email.

Generation is entirely optional: if OPENAI_API_KEY isn't configured, or the
call fails for any reason, generate_hero_image() returns None and the
caller falls back to a text-first email rather than erroring out the whole
demo.
"""
import logging
import os

logger = logging.getLogger("image_gen")

DEFAULT_MODEL = "gpt-image-1"


def generate_hero_image(prompt: str) -> str | None:
    """Generate a hero image from `prompt`. Returns a data: URL (base64 PNG)
    or a hosted URL on success, or None if generation is unavailable/failed."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.info("OPENAI_API_KEY not set - skipping hero image generation.")
        return None

    model = os.environ.get("OPENAI_IMAGE_MODEL", DEFAULT_MODEL)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        result = client.images.generate(
            model=model,
            prompt=prompt,
            size="1024x1024",
            n=1,
        )
        image = result.data[0]
        b64 = getattr(image, "b64_json", None)
        if b64:
            return f"data:image/png;base64,{b64}"
        url = getattr(image, "url", None)
        if url:
            return url
        logger.warning("Hero image generation returned no b64_json or url.")
        return None
    except Exception:
        logger.exception("Hero image generation failed - falling back to text-first.")
        return None
