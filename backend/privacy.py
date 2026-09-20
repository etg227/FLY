from __future__ import annotations
import hashlib, re
from urllib.parse import urlsplit

_HOST_RE = re.compile(r"(?<![\w.-])((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})(?::(\d{1,5}))?")
_IPV4_RE = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?")
_URL_RE = re.compile(r"https?://[^\s\]\[()<>\"']+")

def _tag(kind, value):
    h = hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()[:10]
    return f"<{kind}:{h}>"

def _redact_url(match):
    raw = match.group(0)
    try:
        p = urlsplit(raw)
        suffix = ""
        if p.path and p.path not in ("", "/"):
            suffix = "/…"
        return _tag("url", p.hostname or raw) + suffix
    except Exception:
        return _tag("url", raw)

def redact_log_line(line):
    """Sanitize the persisted diagnostic log.

    The live UI may show full local diagnostics, but fly.log is intended to be
    shareable and therefore must not contain browsing targets, subscription
    hosts/tokens, or node server addresses.
    """
    text = str(line)
    text = _URL_RE.sub(_redact_url, text)
    text = _IPV4_RE.sub(lambda m: _tag("ip", m.group(0)), text)
    text = _HOST_RE.sub(lambda m: _tag("host", m.group(1)) + (f":{m.group(2)}" if m.group(2) else ""), text)
    return text
