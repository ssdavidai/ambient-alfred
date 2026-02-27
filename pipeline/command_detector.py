"""Command detection using OpenRouter API.

Classifies whether a transcript contains a direct command to the agent.
Uses OpenRouter so the user can pick any model they want.
"""

import logging
import re

import httpx

from pipeline.config import AGENT_NAME, OPENROUTER_API_KEY, OPENROUTER_MODEL

logger = logging.getLogger(__name__)

# Keyword patterns for quick pre-filter
_KEYWORD_PATTERN = None


def _get_keyword_pattern() -> re.Pattern:
    global _KEYWORD_PATTERN
    if _KEYWORD_PATTERN is None:
        name = re.escape(AGENT_NAME.lower())
        # Support accented variants (e.g., Alfred/Alfréd)
        pattern = name.replace("e", "[eé]")
        _KEYWORD_PATTERN = re.compile(rf"\b{pattern}\w*\b", re.IGNORECASE)
    return _KEYWORD_PATTERN


def has_agent_mention(text: str) -> bool:
    """Quick regex check for agent name mentions in text."""
    return bool(_get_keyword_pattern().search(text))


def classify_command(text: str) -> bool:
    """Use OpenRouter API to classify if text is a direct command to the agent.

    Returns True if it's a command, False otherwise.
    """
    if len(text.strip()) < 3:
        return False

    if not OPENROUTER_API_KEY:
        logger.warning("OPENROUTER_API_KEY not set, falling back to keyword detection")
        return has_agent_mention(text)

    agent = AGENT_NAME
    prompt = (
        f"Is this text a direct command, request, or instruction to an AI assistant named {agent}? "
        f"It could be in any language. "
        f"Examples of YES: '{agent} call me', '{agent} remind me to buy milk', "
        f"'{agent} check my calendar'. "
        f"Examples of NO: '{agent} did something funny today', 'I told {agent} about it', "
        f"casual conversation mentioning {agent}, background noise. "
        f"Reply with ONLY 'YES' or 'NO'.\n\nText: {text}"
    )

    try:
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 5,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            result = resp.json()
            answer = (
                result.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
                .upper()
            )
            return answer == "YES"
        else:
            logger.warning(f"OpenRouter API error: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"Command classifier failed: {e}")

    return False
