"""AgentCore Platform v1.0"""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes, never the full state
#  - Return AgentStatus enum constants, never plain strings
#  - Read the structured parameters via state.get("input_context", {}), read-only
#  - Never import from mediator/, api/, or other agents

import re
from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json, to_json

# Certification marks this template recognises. A closed list: the reported
# certifications are always a subset of it, which is the other half of the
# output invariant the boundary enforces. Nothing from the document text is
# ever reported as a certification unless it appears here.
KNOWN_CERTIFICATIONS: frozenset[str] = frozenset(
    {
        "JAS",
        "有機JAS",
        "ISO22000",
        "FSSC22000",
        "HACCP",
        "JIS",
        "GLOBALG.A.P.",
        "SQF",
        "BRC",
        "IFS",
    }
)

_DEFAULT_MAX_CERTIFICATIONS = 16

# The certification label, bounded at the end of its line so a single-line
# parse result does not fold the rest of the document into it.
_CERT_LABEL_RE = re.compile(r"認証[：:]\s*([^\n]*)")


def extract_certifications(text: str) -> List[str]:
    """Return the recognised certification marks present in the text.

    Both the whole document and the certification label are scanned, then the
    result is intersected with the closed list, so an unrecognised string in
    the document can never become a reported certification.
    """
    found: set[str] = {cert for cert in KNOWN_CERTIFICATIONS if cert in text}
    label = _CERT_LABEL_RE.search(text)
    if label:
        segment = label.group(1)
        found.update(cert for cert in KNOWN_CERTIFICATIONS if cert in segment)
    return sorted(found)


class CertValidateNode(FunctionNode):
    """Inner pipeline: identify the supplier certifications named in the document.

    Recognition is pattern based against a closed list of marks. It establishes
    that a mark is CLAIMED in the document, not that the claim is valid — a
    registry lookup would be required for that, and this template does not
    perform one. The catalog entry names the field accordingly.

    Trust level: the outer entry gate is the trust boundary for the request;
    inner pipeline nodes run below it.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def _max_certifications(self, state: Dict[str, Any]) -> int:
        tuning: Any = from_json(state.get("extraction_tuning"), {})
        value = tuning.get("max_certifications") if isinstance(tuning, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
        return _DEFAULT_MAX_CERTIFICATIONS

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        raw_text = state.get("raw_text", "")

        if not raw_text or not isinstance(raw_text, str):
            emit_trace_event("cert_validate_failed", {"reason": "missing_raw_text"}, state)
            return {
                "status": AgentStatus.ERROR,
                "error_log": ["CertValidateNode: raw_text is missing — run DocParseNode first"],
            }

        found = extract_certifications(raw_text)[: self._max_certifications(state)]
        cert_compliance = bool(found)

        emit_trace_event(
            "cert_validate_complete",
            {
                # The marks come from a closed list declared in this module, so
                # naming them in the audit record carries no caller content.
                "found_certifications": found,
                "cert_compliance": cert_compliance,
            },
            state,
        )

        return {
            "certifications": to_json(found),
            "cert_compliance": cert_compliance,
            "status": AgentStatus.SUCCESS,
        }
