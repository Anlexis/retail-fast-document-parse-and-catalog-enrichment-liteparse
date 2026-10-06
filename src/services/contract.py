"""AgentCore Platform v1.0"""

# The caller-data contract for the structured invocation parameters.
#
# The contract is a CLOSED set of four fields, declared once here and read by
# both the HTTP adapter and the node that validates them, so the adapter can
# drop what the contract does not declare and the node can bound what it does.
#
# Why the contract is closed rather than open:
#
#   A validator that merely IGNORES an undeclared key does not remove it. The
#   key stays in the structured parameters, the backbone's first node copies
#   those verbatim into its own result, and the framework's mandatory output
#   gate scans every value of every result — so one credential-shaped string
#   under a key nobody declared fails the FIRST node of the graph, before any
#   template code runs, with nothing in the response naming the cause. Dropping
#   unknown keys at the boundary is what makes "the contract only accepts inert
#   identifiers" true rather than aspirational.
#
#   It is not sufficient on its own: `document_text` is a DECLARED field that
#   legitimately carries a whole supplier document, and a document plausibly
#   contains a long token-shaped run. That is why the adapter also screens the
#   declared fields with the framework's own credential detector and refuses
#   readably, naming the field and never the value.
#
# Why the document text travels here rather than in the request string:
#
#   The platform rewrites personal-data shapes out of the request string at
#   every node boundary, and its name heuristic reads title-case proper nouns
#   as personal names. A supplier specification sent that way arrives with its
#   product names replaced by a redaction sentinel. This channel is not
#   rewritten, so the extraction sees the document the caller sent.
#
# Every value that reaches the rendered catalog entry is bounded here: a length
# cap, and for the fields that are rendered verbatim, an inert alphabet. A
# rejected value is never echoed — errors name the field only.

from __future__ import annotations

import re
from typing import Any, Dict, Tuple

# Field -> maximum length in characters. `document_text` is bounded by runtime
# configuration instead (it is the one field whose useful size is deployment
# dependent); the value here is the absolute ceiling the validator will accept
# however the configuration is set.
MAX_DOCUMENT_TEXT_CHARS = 200_000
MAX_DOCUMENT_URL_CHARS = 2_048
MAX_SUPPLIER_CODE_CHARS = 32

#: The complete set of accepted structured-parameter field names. Anything else
#: is dropped at the adapter before the agent is invoked.
CONTEXT_FIELDS: Tuple[str, ...] = (
    "document_text",
    "document_url",
    "document_type",
    "supplier_code",
)

#: Document formats the parse layer understands.
SUPPORTED_DOCUMENT_TYPES: Tuple[str, ...] = ("PDF", "IMAGE", "EXCEL", "CSV")

# A URL is rendered back into the catalog entry, so it is restricted to the
# characters a URL is actually made of. Anything outside that set is refused
# rather than escaped, because escaping invites a second decoding step.
_URL_RE = re.compile(r"^[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{1," + str(MAX_DOCUMENT_URL_CHARS) + r"}$")

# The supplier code is rendered verbatim into the catalog entry, so it is held
# to an inert identifier alphabet: free text there is caller-controlled output.
_SUPPLIER_CODE_RE = re.compile(r"^[a-z0-9_]{1," + str(MAX_SUPPLIER_CODE_CHARS) + r"}$")


class ContractError(ValueError):
    """A caller field failed validation. Carries the field name, never the value."""


def select_declared(raw: Any) -> Dict[str, Any]:
    """Return only the declared fields from a caller mapping.

    Unknown keys are DROPPED, not ignored — see the module note. A non-mapping
    is an empty contract rather than an error, so a caller that sends nothing
    structured still reaches the document-reference path.
    """
    if not isinstance(raw, dict):
        return {}
    return {key: raw[key] for key in CONTEXT_FIELDS if key in raw}


def _require_str(field: str, value: Any, limit: int) -> str:
    """Return a bounded string, or raise naming the FIELD only."""
    if not isinstance(value, str):
        raise ContractError(f"{field} must be a string")
    if len(value) > limit:
        raise ContractError(f"{field} exceeds the maximum length of {limit} characters")
    return value


def validate_contract(raw: Any, max_document_chars: int) -> Dict[str, Any]:
    """Validate the declared fields and return the bounded contract.

    `max_document_chars` comes from runtime configuration and bounds the
    document text a deployment is willing to process; it is clamped to the
    absolute ceiling above so a misconfigured value cannot widen the contract.

    Raises ContractError naming the offending field. No value is ever included
    in the message.
    """
    declared = select_declared(raw)
    contract: Dict[str, Any] = {}

    ceiling = max(1, min(int(max_document_chars), MAX_DOCUMENT_TEXT_CHARS))

    if "document_text" in declared:
        contract["document_text"] = _require_str("document_text", declared["document_text"], ceiling)

    if "document_url" in declared:
        url = _require_str("document_url", declared["document_url"], MAX_DOCUMENT_URL_CHARS)
        if url and not _URL_RE.match(url):
            raise ContractError("document_url contains characters outside the accepted URL alphabet")
        contract["document_url"] = url

    if "document_type" in declared:
        doc_type = _require_str("document_type", declared["document_type"], 16).strip().upper()
        if doc_type not in SUPPORTED_DOCUMENT_TYPES:
            raise ContractError("document_type must be one of " + ", ".join(SUPPORTED_DOCUMENT_TYPES))
        contract["document_type"] = doc_type

    if "supplier_code" in declared:
        code = _require_str("supplier_code", declared["supplier_code"], MAX_SUPPLIER_CODE_CHARS)
        if code and not _SUPPLIER_CODE_RE.match(code):
            raise ContractError(
                "supplier_code must match the inert identifier pattern [a-z0-9_] (max "
                f"{MAX_SUPPLIER_CODE_CHARS} characters)"
            )
        contract["supplier_code"] = code

    return contract
