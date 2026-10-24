"""Local Qwen inference: Qwen-Agent plus Ollama structured-output recovery.

Qwen-Agent handles the standard LLM call. If a thinking model returns no
usable JSON, retry through Ollama's native structured JSON chat endpoint.
No OpenAI account or key is involved.
"""
import json
import logging
import re
from urllib.parse import urlsplit

import httpx

from . import settings

log = logging.getLogger(__name__)


class QwenResponseError(RuntimeError):
    """Qwen did not produce a usable structured response."""


def _json_object(content: str) -> dict:
    """Extract a JSON object, ignoring fenced markup and Qwen thinking tags."""
    if not isinstance(content, str):
        raise QwenResponseError("Qwen response content is not text")
    # Thinking is not a final answer, and may contain examples with braces.
    cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char == "{":
            try:
                value, _ = decoder.raw_decode(cleaned[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    raise QwenResponseError("Qwen response contains no valid JSON object")


def _ollama_json_fallback(messages: list[dict]) -> dict:
    """Use documented Ollama native settings: think=false and format=json."""
    server = urlsplit(settings.QWEN_MODEL_SERVER)
    if server.scheme not in ("http", "https") or not server.netloc or server.path.rstrip("/") != "/v1":
        raise QwenResponseError(
            "Qwen output was not JSON; the Ollama fallback requires a model server URL ending in /v1"
        )
    url = f"{server.scheme}://{server.netloc}/api/chat"
    try:
        result = httpx.post(
            url,
            json={
                "model": settings.QWEN_MODEL,
                "messages": messages,
                "stream": False,
                "think": False,
                "format": "json",
                "options": {"temperature": 0.1, "num_predict": 1800},
            },
            timeout=180.0,
        )
        result.raise_for_status()
        response = result.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise QwenResponseError(
            f"Ollama structured-output retry failed ({type(exc).__name__}); verify Ollama is running"
        ) from exc
    content = (response.get("message") or {}).get("content", "")
    return _json_object(content)


def generate_json(instructions: str, payload: dict) -> dict:
    """Get JSON from a local Qwen model with one guarded recovery attempt."""
    if not settings.QWEN_MODEL or not settings.QWEN_MODEL_SERVER:
        raise RuntimeError("Configure QWEN_MODEL and QWEN_MODEL_SERVER")
    from qwen_agent.llm import get_chat_model

    llm = get_chat_model({
        "model": settings.QWEN_MODEL,
        "model_server": settings.QWEN_MODEL_SERVER,
        # Only use this with a trusted, local/private Ollama service.
        "api_key": "EMPTY",
        "generate_cfg": {"temperature": 0.2, "max_tokens": 1600},
    })
    messages = [
        {
            "role": "system",
            "content": instructions + "\nReturn only one valid JSON object without Markdown.",
        },
        {
            "role": "user",
            # Qwen3 honors the most recent /no_think control in the messages.
            "content": json.dumps(payload, ensure_ascii=False)[:9000]
            + "\n/no_think\nRespond with the JSON object now.",
        },
    ]
    responses = llm.chat(messages=messages, stream=False)
    if responses:
        last = responses[-1]
        content = last.get("content") if isinstance(last, dict) else getattr(last, "content", None)
        try:
            return _json_object(content)
        except QwenResponseError:
            pass

    # Qwen3 thinking can consume the output budget, leaving message.content
    # empty. Recover with Ollama's explicit no-thinking, JSON-formatted API.
    log.warning("Qwen-Agent returned no valid JSON; retrying with Ollama structured output")
    return _ollama_json_fallback(messages)
