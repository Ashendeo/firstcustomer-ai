"""Local Qwen model adapter using Qwen-Agent and an Ollama OpenAI-compatible endpoint.

Qwen-Agent is the orchestration/LLM interface; Ollama serves the open-weight model.
No OpenAI API account, key or credits are required.
"""
import json

from . import settings


class QwenResponseError(RuntimeError):
    """The model did not produce a usable structured response."""


def _json_object(content: str) -> dict:
    """Parse an object even if a small model adds fences or a thinking preamble."""
    if not isinstance(content, str):
        raise QwenResponseError("Qwen response content is not text")
    start = content.find("{")
    if start < 0:
        raise QwenResponseError("Qwen response contains no JSON object")
    try:
        value, _ = json.JSONDecoder().raw_decode(content[start:])
    except json.JSONDecodeError as exc:
        raise QwenResponseError("Qwen returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise QwenResponseError("Qwen response must be a JSON object")
    return value


def generate_json(instructions: str, payload: dict) -> dict:
    """Call a Qwen model using the official Qwen-Agent LLM adapter."""
    if not settings.QWEN_MODEL or not settings.QWEN_MODEL_SERVER:
        raise RuntimeError("Configure QWEN_MODEL and QWEN_MODEL_SERVER")
    from qwen_agent.llm import get_chat_model

    llm = get_chat_model({
        "model": settings.QWEN_MODEL,
        "model_server": settings.QWEN_MODEL_SERVER,
        # Ollama doesn't require authentication for its local HTTP API.
        # Never expose this unauthenticated Ollama endpoint publicly.
        "api_key": "EMPTY",
        "generate_cfg": {"temperature": 0.1, "max_tokens": 1000},
    })
    messages = [
        {
            "role": "system",
            "content": instructions
            + "\nReturn exactly one valid JSON object, without Markdown or commentary. /no_think",
        },
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False)[:9000],
        },
    ]
    responses = llm.chat(messages=messages, stream=False)
    if not responses:
        raise QwenResponseError("Qwen returned an empty response")
    last = responses[-1]
    content = last.get("content") if isinstance(last, dict) else getattr(last, "content", None)
    return _json_object(content)
