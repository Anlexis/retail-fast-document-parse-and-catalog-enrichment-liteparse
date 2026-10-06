"""AgentCore Platform v1.0"""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes, never the full state
#  - Return AgentStatus enum constants, never plain strings
#  - Read the structured parameters via state.get("input_context", {}), read-only
#  - Never import from mediator/, api/, or other agents

import json
import re
from typing import Any, ClassVar, Dict

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json, to_json
from src.services.contract import ContractError, validate_contract
from src.services.screens import injection_finding

_DEFAULT_DOCUMENT_TYPE = "PDF"
_DEFAULT_MAX_DOCUMENT_CHARS = 20000

# A bare document reference: a scheme-qualified URL, or a whitespace-free path.
# Prose does not match, which is the point — see _contract_from_request_string.
_BARE_REFERENCE_RE = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*://\S+|[^\s]+\.[A-Za-z0-9]{1,8})$")


class InputSanitizeNode(FunctionNode):
    """Inner pipeline: settle the document reference and the document text.

    Two sources are merged, contract first:

    - The validated caller contract, seeded into inner state by the graph's
      initial-state hook. It carries the document text when the caller sent
      one, plus the reference fields.
    - The request string, which may be a JSON object naming the document, or a
      bare reference. This is the legacy shape and it still works.

    Anything taken from the request string is validated through the SAME
    contract validator the outer node uses, so the two entry shapes cannot
    diverge into two different sets of bounds. Both are screened for injection
    again here: this node is reachable from the inner graph directly, and a
    check that only runs on one path is a check that is absent on the other.

    Trust level: the outer entry gate is the trust boundary for the request;
    inner pipeline nodes run below it.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def _reject(self, state: Dict[str, Any], reason: str, message: str) -> Dict[str, Any]:
        emit_trace_event("input_sanitize_failed", {"reason": reason}, state)
        return {
            "status": AgentStatus.ERROR,
            "error_log": [f"InputSanitizeNode: {message}"],
        }

    def _max_document_chars(self, state: Dict[str, Any]) -> int:
        """Read the configured ceiling from the seeded extraction tuning."""
        extraction = from_json(state.get("extraction_tuning"), {})
        value = extraction.get("max_document_chars") if isinstance(extraction, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
        return _DEFAULT_MAX_DOCUMENT_CHARS

    def _contract_from_request_string(self, raw: Any, limit: int) -> Dict[str, Any]:
        """Parse the legacy request shape into contract fields.

        Two shapes are recognised: a JSON object naming the document, and a
        bare document reference. Anything else — a sentence describing what the
        caller wants — yields NO contract fields, so it cannot be mistaken for
        a document reference and rendered back into the catalog entry as one.
        That distinction matters: without it, "enrich this supplier document"
        became the entry's document_url, or was refused for containing spaces.

        Whatever is recognised still goes through validate_contract, so this
        path and the structured path share one set of bounds.
        """
        if not isinstance(raw, str) or not raw.strip():
            return {}
        text = raw.strip()
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                return {}
            return validate_contract(parsed, limit) if isinstance(parsed, dict) else {}
        if _BARE_REFERENCE_RE.match(text):
            return validate_contract({"document_url": text}, limit)
        return {}

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        limit = self._max_document_chars(state)
        contract: Dict[str, Any] = dict(from_json(state.get("caller_contract"), {}) or {})

        raw_request = state.get("validated_input", state.get("user_input", ""))

        finding = injection_finding(raw_request)
        if finding:
            return self._reject(
                state,
                f"injection_{finding}",
                "the request contains a disallowed instruction pattern",
            )
        finding = injection_finding(contract)
        if finding:
            return self._reject(
                state,
                f"contract_injection_{finding}",
                "the caller contract contains a disallowed instruction pattern",
            )

        try:
            from_request = self._contract_from_request_string(raw_request, limit)
        except ContractError as exc:
            return self._reject(state, "request_invalid", str(exc))

        # Contract fields win: they were validated at the trust boundary and
        # the request string is the older, less specific channel.
        for key, value in from_request.items():
            contract.setdefault(key, value)

        document_url = str(contract.get("document_url", "") or "")
        document_text = str(contract.get("document_text", "") or "")
        document_type = str(contract.get("document_type", "") or _DEFAULT_DOCUMENT_TYPE)
        supplier_code = str(contract.get("supplier_code", "") or "")

        if not document_url and not document_text:
            return self._reject(
                state,
                "no_document",
                "the request names no document: supply document_url or document_text",
            )

        emit_trace_event(
            "input_sanitize_complete",
            {
                "document_type": document_type,
                "has_caller_text": bool(document_text),
                "document_url_length": len(document_url),
            },
            state,
        )

        # The merged contract is written back so the parse step reads one
        # settled object rather than re-deriving the merge from two channels.
        contract["document_url"] = document_url
        contract["document_type"] = document_type
        if supplier_code:
            contract["supplier_code"] = supplier_code
        if document_text:
            contract["document_text"] = document_text

        return {
            "caller_contract": to_json(contract),
            "document_url": document_url,
            "document_type": document_type,
            "supplier_code": supplier_code,
            "status": AgentStatus.SUCCESS,
        }
