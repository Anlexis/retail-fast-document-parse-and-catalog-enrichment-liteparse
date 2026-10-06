"""AgentCore Platform v1.0"""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes, never the full state
#  - Return AgentStatus enum constants, never plain strings
#  - Read the structured parameters via state.get("input_context", {}), read-only
#  - Never import from mediator/, api/, or other agents

from pathlib import Path
from typing import Any, ClassVar, Dict

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from framework.security.pii_detector import detect_pii
from framework.security.pii_masking import mask_pii
from framework.utils.config_loader import load_agent_config
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import to_json
from src.services.contract import ContractError, validate_contract
from src.services.screens import injection_finding

# Repo root: src/nodes/pre_process_node.py -> parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_MAX_DOCUMENT_CHARS = 20000


class PreProcessNode(FunctionNode):
    """Outer backbone: validate the request and the caller contract.

    Trust gate: the entry contract is VERIFIED_EXTERNAL, so an unauthenticated
    caller is refused here before any document is read.

    Three things happen, in this order:

    1. The request string is screened for prompt injection. The framework's own
       input gate already blocks some forms before this node runs, but only
       where that gate is active; enforcing the refusal here is what makes the
       guarantee the template's own. The tests call execute() directly, with no
       framework wrapper in front, so they measure this node rather than the
       framework.
    2. The structured parameters are reduced to the declared contract and every
       field is bounded. A rejected field is named; its value is never echoed
       into the error log, the audit event or the response.
    3. The document text is run through the platform's personal-data mask. The
       framework masks the request string and the validated request, but not
       the structured-parameter channel — so free text arriving there would
       otherwise skip the scan the same text gets on the other channel.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _max_document_chars(self) -> int:
        """The configured document ceiling, or the built-in default."""
        extraction = load_agent_config(_REPO_ROOT).get("extraction")
        if isinstance(extraction, dict):
            value = extraction.get("max_document_chars")
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                return value
        return _DEFAULT_MAX_DOCUMENT_CHARS

    def _reject(self, state: Dict[str, Any], reason: str, message: str) -> Dict[str, Any]:
        """Refuse with a closed-set audit reason and a field-naming message."""
        emit_trace_event("pre_process_validation_failed", {"reason": reason}, state)
        return {
            "status": AgentStatus.ERROR,
            "error_log": [f"PreProcessNode: {message}"],
        }

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        user_input = state.get("user_input", "")
        input_context = state.get("input_context", {})

        if not isinstance(user_input, str) or not user_input.strip():
            return self._reject(state, "empty_input", "user_input is empty or missing")

        stripped = user_input.strip()

        finding = injection_finding(stripped)
        if finding:
            return self._reject(
                state,
                f"injection_{finding}",
                "user_input contains a disallowed instruction pattern",
            )

        finding = injection_finding(input_context)
        if finding:
            return self._reject(
                state,
                f"context_injection_{finding}",
                "input_context contains a disallowed instruction pattern",
            )

        try:
            contract = validate_contract(input_context, self._max_document_chars())
        except ContractError as exc:
            # ContractError messages name the field and never the value.
            return self._reject(state, "contract_invalid", f"input_context.{exc}")

        document_text = contract.get("document_text", "")
        if document_text:
            findings = detect_pii(document_text)
            if findings:
                contract["document_text"] = mask_pii(document_text, findings)

        emit_trace_event(
            "pre_process_validation_passed",
            {
                # Field NAMES are declared by this template, so they are safe to
                # record; the values they carry are not.
                "contract_fields": sorted(contract),
                "input_length": len(stripped),
                "document_text_masked": bool(document_text) and contract.get("document_text") != document_text,
            },
            state,
        )

        return {
            "validated_input": stripped,
            "caller_contract": to_json(contract),
            "status": AgentStatus.SUCCESS,
        }
