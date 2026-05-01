"""Anthropic Claude wrapper used by the pipeline for vision tasks.

Two patterns are supported:
  - parse_json(image_bytes, system, user) -> dict
    One-shot extraction with strict JSON output. Used by Phase 2 (schedule
    parser) as a fallback when text-based parsing fails.

  - is_available() -> bool
    True if `ANTHROPIC_API_KEY` is set. The pipeline gates vision-using code
    paths on this, so the deterministic path always works without a key.

Prompt caching is on by default for the system prompt and any large reference
text. Vision requests typically cost <$0.10 each on Sonnet 4.6.
"""
from __future__ import annotations
import base64
import json
import os
from typing import Any, Optional

try:
    from anthropic import Anthropic, APIError  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    Anthropic = None  # type: ignore[assignment,misc]
    APIError = Exception  # type: ignore[assignment,misc]


_DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")


def is_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and Anthropic is not None


def _client() -> "Anthropic":
    if Anthropic is None:
        raise RuntimeError("anthropic SDK is not installed")
    return Anthropic()


def parse_json(
    image_bytes: bytes,
    system: str,
    user: str,
    model: str = _DEFAULT_MODEL,
    max_tokens: int = 2048,
) -> dict[str, Any]:
    """Single Claude vision request that must return valid JSON. Returns
    `{}` on any failure (no exception)."""
    if not is_available():
        return {}
    img_b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    try:
        client = _client()
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": img_b64,
                        },
                    },
                    {"type": "text", "text": user},
                ],
            }],
        )
    except Exception as e:  # noqa: BLE001
        return {"_error": f"{type(e).__name__}: {e}"}

    text_parts: list[str] = []
    for block in getattr(resp, "content", []):
        t = getattr(block, "text", None)
        if t:
            text_parts.append(t)
    blob = "\n".join(text_parts).strip()
    # Strip code-fence wrappers if present.
    if blob.startswith("```"):
        blob = blob.strip("`")
        if blob.lower().startswith("json"):
            blob = blob[4:]
        blob = blob.strip()
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        # Try to extract the first {...} block.
        start = blob.find("{")
        end = blob.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(blob[start:end + 1])
            except json.JSONDecodeError:
                pass
        return {"_error": "could not parse JSON", "_raw": blob[:500]}
