"""Explicit opt-in metadata for SDK users; native log content is never inspected."""

import hashlib
import hmac


def prompt_metadata(prompt: str, *, secret: bytes, enabled: bool = False) -> dict:
    """Fingerprint exact supplied text in memory using a caller-managed private key.

    Disabled by default. Use one random >=32-byte secret per installation and keep
    it outside events, dashboards and source control. Rotating it starts a new
    comparison scope. Fingerprints still reveal equality and are sensitive data.
    """
    if not enabled:
        return {}
    if not isinstance(prompt, str):
        raise ValueError("Prompt must be text")
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ValueError("Prompt fingerprint secret must be at least 32 random bytes")
    return {
        "prompt_analysis_opt_in": True,
        "prompt_fingerprint": hmac.new(secret, prompt.encode("utf-8"), hashlib.sha256).hexdigest(),
        "prompt_fingerprint_scope": "hmac-sha256-v1:" + hashlib.sha256(secret).hexdigest()[:16],
        "prompt_characters": len(prompt),
    }
