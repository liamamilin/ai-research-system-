"""
Optional report cleanup pass.

The research agent now writes the final report directly via the LLM API, so
cleanup is disabled by default. When enabled, a cheap model removes any
preamble/meta-commentary that slipped into the report.

Configured via ``system.yaml``::

    extraction:
      enabled: false
      model: "deepseek-v4-flash"
      base_url: "https://opencode.ai/zen/v1"
      api_key_env: "EXTRACTION_API_KEY"
"""

import os
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """You are a report extraction specialist.

Below is raw content that may contain:
- Agent internal dialogue ("Both exploration agents have completed...")
- Tool call logs and execution traces
- Assistant meta-commentary about the report
- Multiple drafts or redundant passages

Your job is to extract ONLY the actual report content.

Rules:
1. Keep ALL structured sections, tables, code blocks, bullet points
2. Preserve original Markdown formatting
3. Remove agent logs, internal dialogue, tool call traces
4. Remove meta-commentary ("The report above covers all findings...")
5. Remove duplicate content
6. If the content appears to be a summary referencing "the report above", return the full report sections that are actually present
7. Output ONLY the cleaned report — no explanations, no notes

---BEGIN RAW CONTENT---
{raw_content}
---END RAW CONTENT---
"""


def _load_extraction_config(sys_config: dict) -> Optional[dict]:
    """Extract extraction config from system config. Returns None if disabled."""
    cfg = sys_config.get("extraction", {})
    if not cfg:
        return None
    if not cfg.get("enabled", False):
        logger.debug("Extraction cleanup disabled")
        return None

    model = cfg.get("model")
    base_url = cfg.get("base_url")
    api_key_env = cfg.get("api_key_env", "EXTRACTION_API_KEY")
    api_key = os.environ.get(api_key_env)

    if not model or not base_url:
        logger.warning("extraction.model or extraction.base_url not configured")
        return None
    if not api_key:
        logger.warning(
            "Environment variable '%s' not set; extraction disabled",
            api_key_env,
        )
        return None

    return {
        "model": model,
        "base_url": base_url.rstrip("/"),
        "api_key": api_key,
    }


def _call_extraction_api(
    raw_content: str,
    config: dict,
    timeout: int = 120,
) -> Optional[str]:
    """Call the extraction model API using OpenAI-compatible chat completions."""
    import urllib.request
    import urllib.error

    prompt = EXTRACTION_PROMPT.format(raw_content=raw_content)
    url = f"{config['base_url']}/chat/completions"

    payload = {
        "model": config["model"],
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 32768,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config['api_key']}",
        "User-Agent": "BaseCodingCLi/1.0",
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        logger.error("Extraction API call failed: %s", e)
        return None

    if "error" in body:
        logger.error("Extraction API error: %s", body["error"])
        return None

    choices = body.get("choices", [])
    if not choices:
        logger.error("Extraction API returned no choices")
        return None

    content = choices[0].get("message", {}).get("content", "")
    if not content:
        logger.error("Extraction API returned empty content")
        return None

    return content.strip()


def extract_report(
    raw_content: str,
    sys_config: dict,
    cancel_token=None,
) -> str:
    """Clean the raw model output into the final report.

    Returns the cleaned Markdown content. If extraction is disabled,
    not configured, or fails, returns the original content unchanged.
    """
    if cancel_token and cancel_token.is_set():
        return raw_content

    ext_config = _load_extraction_config(sys_config)
    if not ext_config:
        logger.debug(
            "Extraction cleanup unavailable; keeping raw content as-is"
        )
        return raw_content

    if not raw_content or len(raw_content.strip()) < 100:
        return raw_content

    # Check cancel before the API call (which may be slow)
    if cancel_token and cancel_token.is_set():
        return raw_content

    logger.info(
        "Calling extraction API (%s) on %d bytes of raw content...",
        ext_config["model"],
        len(raw_content),
    )

    cleaned = _call_extraction_api(raw_content, ext_config)

    if cleaned is None:
        logger.warning("Extraction failed, keeping raw content")
        return raw_content

    if len(cleaned) < 50:
        logger.warning(
            "Extraction returned suspiciously short result (%d bytes), "
            "keeping raw content",
            len(cleaned),
        )
        return raw_content

    logger.info(
        "Extraction successful: %d → %d bytes (%.0f%% reduction)",
        len(raw_content),
        len(cleaned),
        (1 - len(cleaned) / len(raw_content)) * 100 if raw_content else 0,
    )
    return cleaned
