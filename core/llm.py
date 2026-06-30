"""
Claude API client + key handling for the notification-assessment engine.

The key is read from the ANTHROPIC_API_KEY environment variable, or from
config/api_key.txt (git-ignored). Model + reasoning settings are centralised
here so the rest of the app stays provider-agnostic at the call sites.
"""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY_FILE = os.path.join(ROOT, "config", "api_key.txt")

# Most capable model — legal reasoning is intelligence-sensitive.
MODEL = "claude-opus-4-8"


def get_api_key():
    """Raw key from env (preferred) or config/api_key.txt, or None."""
    k = os.environ.get("ANTHROPIC_API_KEY")
    if k and k.strip():
        return k.strip()
    try:
        with open(KEY_FILE, encoding="utf-8") as fh:
            v = fh.read().strip()
            return v or None
    except FileNotFoundError:
        return None


def looks_real(key):
    """A real Anthropic key starts with sk-ant- and is long; reject placeholders."""
    if not key:
        return False
    k = key.strip()
    low = k.lower()
    if any(bad in low for bad in ("your-key", "your_key", "paste", "xxxx", "<", "example")):
        return False
    return k.startswith("sk-ant-") and len(k) >= 24


def has_key():
    return looks_real(get_api_key())


def key_status():
    """'ok' (usable), 'placeholder' (file has dummy text), or 'none'."""
    k = get_api_key()
    if not k:
        return "none"
    return "ok" if looks_real(k) else "placeholder"


def get_client():
    import anthropic
    key = get_api_key()
    if not key:
        raise RuntimeError("No Anthropic API key configured.")
    return anthropic.Anthropic(api_key=key)
