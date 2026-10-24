"""Single-pass, bounded JSON inference using the *local* Qwen model in Ollama.

Qwen-Agent's general-purpose chat path could return only thinking tokens, then
a second fallback call took a long time for every prospect. For structured
planning/evaluation we call Ollama directly with thinking disabled and
JSON output constrained; there are no recursive or implicit retry loops.

Qwen-Agent remains an option for future multi-tool agents; it isn't required
for these simple JSON extraction tasks.
"""
import json
import logging
import re
from time import monotonic
from urllib.parse import urlsplit

import httpx

from . import settings

log = logging.getLogger(__name__)


class QwenResponseError(RuntimeError):
    """Local Qwen model failed to provide a valid JSON response."""


def _json_object(content: str) -> dict:
    if not isinstance(content, str):
        raise QwenResponseError("Qwen response content is not text")
    # Ignore completed Qwen3 reasoning sections (including possible JSON samples).
    cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    decoder = json.JSONDecoder()
    for position, ch in enumerate(cleaned):
        if ch != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(cleaned[position:])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    raise QwenResponseError("Qwen returned no valid JSON object")


def _native_ollama_url() -> str:
    parsed = urlsplit(settings.QWEN_MODEL_SERVER)
    if (parsed.scheme not in ("http", "https") or not parsed.netloc or
            parsed.path.rstrip("/") not in ("", "/v1") or
            parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise QwenResponseError(
            "QWEN_MODEL_SERVER must be a trusted Ollama endpoint, "
            "such as http://localhost:11434/v1"
        )
    return f"{parsed.scheme}://{parsed.netloc}/api/chat"


def generate_json(instructions: str, payload: dict) -> dict:
    """Exactly ONE request per invocation; fail clearly if inference is slow."""
    if not settings.QWEN_MODEL:
        raise QwenResponseError("QWEN_MODEL is not configured")

    url = _native_ollama_url()
    messages = [
        {"role": "system",
         "content": instructions + "\nReturn only a valid JSON object with the requested fields."},
        {"role": "user",
         "content": json.dumps(payload, ensure_ascii=False, default=str)[:7000]},
    ]
    body = {
        "model": settings.QWEN_MODEL,
        "messages": messages,
        "stream": False,
        "think": False,
        "format": "json",
        "options": {
            "temperature": 0,
            "num_predict": settings.QWEN_NUM_PREDICT,
        },
        "keep_alive": "5m",
    }
    start = monotonic()
    try:
        # Ollama non-streaming responses can take time on CPU. One request only.
        result = httpx.post(url, json=body, timeout=httpx.Timeout(
            connect=5.0, read=settings.QWEN_TIMEOUT_SECONDS,
            write=15.0, pool=5.0))
        result.raise_for_status()
        data = result.json()
    except httpx.TimeoutException as exc:
        raise QwenResponseError(
            f"Ollama inference timed out after {settings.QWEN_TIMEOUT_SECONDS}s. "
            "Check 'ollama ps', available RAM and QWEN_MODEL size."
        ) from exc
    except httpx.HTTPError as exc:
        raise QwenResponseError(
            f"Ollama request failed ({type(exc).__name__}). Check Ollama is running "
            f"and the configured model '{settings.QWEN_MODEL}' is installed."
        ) from exc
    except ValueError as exc:
        raise QwenResponseError("Ollama returned an invalid HTTP JSON response") from exc

    duration = monotonic() - start
    if not isinstance(data, dict):
        raise QwenResponseError("Ollama HTTP response did not contain an object")
    content = (data.get("message") or {}).get("content") or ""
    reason = data.get("done_reason", "unknown")
    log.info(
        "Ollama finished in %.1fs, reason=%s, generated_tokens=%s",
        duration, reason, data.get("eval_count", "?"),
    )
    if not content.strip():
        raise QwenResponseError(
            f"Ollama returned an empty answer after {duration:.1f}s "
            f"(reason={reason}). Try lowering QWEN_NUM_PREDICT, checking memory "
            "pressure, or choosing a smaller non-thinking Qwen model."
        )
    return _json_object(content)
