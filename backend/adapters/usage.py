"""Normalize reported usage without adding overlapping categories."""


def count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("token counts must be nonnegative integers")
    return value


def normalize_usage(provider: str, usage: dict) -> dict:
    if provider not in (
        "anthropic",
        "claude",
        "gemini",
        "google",
        "ollama",
        "openai",
        "openrouter",
        "openai-compatible",
    ):
        raise ValueError("Unsupported usage semantics; provide an explicit normalizer")
    if not isinstance(usage, dict) or not usage:
        raise ValueError("Usage metadata unavailable")
    result = dict(
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        reasoning_tokens=0,
        input_includes_cache=True,
        output_includes_reasoning=True,
    )
    if provider in ("anthropic", "claude"):
        mapping = {
            "input_tokens": "input_tokens",
            "output_tokens": "output_tokens",
            "cache_read_tokens": "cache_read_input_tokens",
            "cache_creation_tokens": "cache_creation_input_tokens",
        }
        result["input_includes_cache"] = False
        total_key = "total_tokens"
    elif provider in ("gemini", "google"):
        mapping = {
            "input_tokens": "promptTokenCount",
            "output_tokens": "candidatesTokenCount",
            "cache_read_tokens": "cachedContentTokenCount",
            "reasoning_tokens": "thoughtsTokenCount",
        }
        result["output_includes_reasoning"] = False
        total_key = "totalTokenCount"
    elif provider == "ollama":
        mapping = {"input_tokens": "prompt_eval_count", "output_tokens": "eval_count"}
        total_key = "total_tokens"
    else:
        mapping = {
            "input_tokens": "input_tokens" if "input_tokens" in usage else "prompt_tokens",
            "output_tokens": "output_tokens" if "output_tokens" in usage else "completion_tokens",
        }
        details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
        result["cache_read_tokens"] = count(details.get("cached_tokens", 0))
        details = usage.get("output_tokens_details") or usage.get("completion_tokens_details") or {}
        result["reasoning_tokens"] = count(details.get("reasoning_tokens", 0))
        total_key = "total_tokens"
    for destination, source in mapping.items():
        result[destination] = count(usage.get(source, 0))
    derived = result["input_tokens"] + result["output_tokens"]
    if not result["input_includes_cache"]:
        derived += result["cache_read_tokens"] + result["cache_creation_tokens"]
    if not result["output_includes_reasoning"]:
        derived += result["reasoning_tokens"] + count(usage.get("toolUsePromptTokenCount", 0))
    result["total_tokens"] = count(usage[total_key]) if usage.get(total_key) is not None else derived
    return result
