"""AgentCore Platform v1.0"""

# Content screens shared by the nodes that own the caller contract.
#
# Two screens live here, both applied to caller-supplied text before it can
# reach the extraction pipeline or the rendered catalog entry.
#
# 1. Prompt-injection screen (`injection_finding`)
#
#    Chat-template CONTROL TOKENS are screened as a class, not as a list of
#    phrases. `<|im_start|>`, `[INST]` and `<<SYS>>` are the instruction
#    delimiters of the common chat templates: a supplier document containing
#    them is trying to open a new turn, whatever words follow. A screen built
#    only from directive phrases misses the token form entirely, which is the
#    form an attacker reaches for first.
#
#    The framework's own input gate blocks two of those three before template
#    code runs, and scores `<<SYS>>` as no finding — but only where that gate
#    is active. A template that relies on it is fail-OPEN wherever it is absent
#    or configured off, so the guarantee is enforced here, in the node that
#    owns the caller contract, and proven by calling execute() directly.
#
#    Every string is screened TWICE: once raw, so a control token is seen
#    before markup stripping can delete it, and once with markup removed, so a
#    directive spliced apart by inert tags (`ig<b>nore all previous
#    instructions`) is seen after the strip reassembles it. Stripping markup
#    and forwarding the residue is not refusal — it converts a detectable
#    attack into undetectable plain text.
#
#    Directive patterns are anchored on both ends and require a target noun, so
#    ordinary supplier wording ("この表示は以前の規格を置き換えます", "ignore
#    damaged units") does not trip a screen that would then refuse a legitimate
#    catalog request. Screens that fire on real domain text are the failure
#    mode that blocks real work, so both directions are probed in the tests.
#
#    Structured payloads are walked depth first and KEYS are screened as well
#    as values: a JSON `\u`-escape cannot survive a post-parse scan, and a
#    hostile field name is caller-controlled text like any other.
#
# 2. Credential screen (`credential_findings`)
#
#    The UNION of the framework's detector and this module's own patterns,
#    never one in place of the other.
#
#    The framework's patterns describe credential FORMATS — `AKIA…`, `sk_live_`,
#    `sk-…`, JWTs, database URIs — and match none of the `password=…` /
#    `api_key=…` assignment shapes a document plausibly carries. The local
#    patterns are the mirror image: they match the assignment shapes and none
#    of the formats. Using either alone is a narrower gate than using both,
#    and a narrower output gate is a containment bypass rather than a
#    simplification.

from __future__ import annotations

import re
from typing import Any, Iterable

from framework.security.credential_detector import detect_credentials_in_value

# ── Injection screen ─────────────────────────────────────────────────────────

# Chat-template control tokens, as a class. The first alternative covers every
# `<|…|>` delimiter (im_start, im_end, endoftext, system, …) rather than naming
# the ones seen so far.
_CONTROL_TOKEN_RE = re.compile(
    r"<\|[^|>]{0,64}\|>" r"|\[/?INST\]" r"|<</?SYS>>" r"|<\|?(?:endoftext|startoftext)\|?>",
    re.IGNORECASE,
)

# Executable / scripting markers. A supplier document has no reason to carry
# any of them, so these are matched on the raw text.
_MARKUP_MARKER_RE = re.compile(
    r"</?script\b|javascript:|data:text/html|\beval\s*\(|__import__|\bos\.system\s*\(",
    re.IGNORECASE,
)

# Directive phrases. Both ends are constrained: an imperative verb, an optional
# scope word, and a REQUIRED target noun. Without the target noun, "ignore" and
# "disregard" appear in ordinary supplier prose ("ignore damaged units") and the
# screen would refuse real catalog requests.
_DIRECTIVE_RE = re.compile(
    r"\b(?:ignore|disregard|forget|override|bypass)\b"
    r"(?:\s+\b(?:all|any|the|your|every|previous|prior|above|earlier|preceding|system)\b){1,4}"
    r"\s+\b(?:instruction|instructions|rule|rules|prompt|prompts|direction|directions|"
    r"guideline|guidelines|constraint|constraints|policy|policies)\b"
    r"|\byou\s+are\s+now\s+(?:a|an|the)?\s*\b(?:unrestricted|jailbroken|developer\s+mode)\b"
    r"|\bact\s+as\s+(?:a|an|the)\s+\w+\s+(?:and|then)\s+(?:reveal|print|output|disclose)\b"
    r"|\b(?:reveal|print|output|disclose|repeat)\s+(?:your|the)\s+"
    r"(?:system\s+prompt|initial\s+instructions|hidden\s+instructions)\b",
    re.IGNORECASE,
)

# Inert-tag stripper. Only tags are removed; the surrounding characters are
# joined so a directive split across tags is reassembled rather than dropped.
_TAG_RE = re.compile(r"<[^<>]{0,200}>")

# Findings are reported as short closed-set labels. The offending text is never
# carried in a finding, because findings travel into error logs and audit
# events, and a rejected value must not be echoed anywhere.
_CONTROL_TOKEN = "control_token"
_MARKUP_MARKER = "markup_marker"
_DIRECTIVE = "directive"


def _strip_markup(text: str) -> str:
    """Remove inert tags, joining the remainder so split directives reassemble."""
    return _TAG_RE.sub("", text)


def _screen_text(text: str) -> str | None:
    """Return a closed-set finding label for one string, or None.

    Raw first — the markup strip would delete a control token before it could
    be seen. Stripped second — a directive interrupted by tags is only visible
    once they are gone.
    """
    if _CONTROL_TOKEN_RE.search(text):
        return _CONTROL_TOKEN
    if _MARKUP_MARKER_RE.search(text):
        return _MARKUP_MARKER
    if _DIRECTIVE_RE.search(text):
        return _DIRECTIVE
    stripped = _strip_markup(text)
    if stripped != text:
        if _CONTROL_TOKEN_RE.search(stripped):
            return _CONTROL_TOKEN
        if _DIRECTIVE_RE.search(stripped):
            return _DIRECTIVE
    return None


def injection_finding(payload: Any) -> str | None:
    """Screen a value depth first, keys included; return a label or None.

    Mappings, sequences and scalars are all walked. Keys are screened because a
    field name is caller-controlled text, and because a `\\u`-escaped payload
    reaches this scan already decoded — there is nothing left to hide behind.

    Fails closed: an unwalkable value is stringified and screened rather than
    skipped.
    """
    if isinstance(payload, str):
        return _screen_text(payload)
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(key, str):
                found = _screen_text(key)
                if found:
                    return found
            found = injection_finding(value)
            if found:
                return found
        return None
    if isinstance(payload, (list, tuple, set)):
        for item in payload:
            found = injection_finding(item)
            if found:
                return found
        return None
    if payload is None or isinstance(payload, (bool, int, float)):
        return None
    return _screen_text(str(payload))


# ── Credential screen ────────────────────────────────────────────────────────

# Assignment shapes the framework's format-based detector does not describe.
# Each is kept BECAUSE the framework misses it; dropping them in favour of
# "delegating to the framework" would narrow the gate.
_LOCAL_CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("api_key_assignment", re.compile(r"(?i)\bapi[_\-]?key\s*[:=]\s*\S{8,}")),
    ("password_assignment", re.compile(r"(?i)\bpass(?:word|wd)?\s*[:=]\s*\S{4,}")),
    ("secret_assignment", re.compile(r"(?i)\bsecret\s*[:=]\s*\S{8,}")),
    ("token_assignment", re.compile(r"(?i)\b(?:access|auth|bearer)[_\-]?token\s*[:=]\s*\S{8,}")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
)


def _local_credential_types(text: str) -> Iterable[str]:
    for label, pattern in _LOCAL_CREDENTIAL_PATTERNS:
        if pattern.search(text):
            yield label


def credential_findings(value: Any) -> list[str]:
    """Return the union of framework and local credential finding TYPES.

    Types only — never the matched text, and never the value. The framework
    detector recurses through mappings and lists itself; the local patterns are
    applied to the value's string form so a nested assignment shape is covered
    too.
    """
    found: list[str] = []
    for finding in detect_credentials_in_value(value) or []:
        label = finding.get("type") if isinstance(finding, dict) else None
        found.append(str(label or "framework_credential"))
    text = value if isinstance(value, str) else repr(value)
    found.extend(_local_credential_types(text))
    # Stable order, no duplicates — findings are compared in tests.
    return sorted(set(found))
