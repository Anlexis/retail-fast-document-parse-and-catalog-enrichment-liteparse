"""AgentCore Platform v1.0"""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes, never the full state
#  - Return AgentStatus enum constants, never plain strings
#  - Read the structured parameters via state.get("input_context", {}), read-only
#  - Never import from mediator/, api/, or other agents

from typing import Any, ClassVar, Dict

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json
from src.services.service import DocumentSource

_DEFAULT_MAX_DOCUMENT_CHARS = 20000


class DocParseNode(FunctionNode):
    """Inner pipeline: settle the document text this request is extracted from.

    The text comes from the caller when they sent one, from the parse sidecar
    when it is reachable, and otherwise from a fixed illustrative document. The
    branch taken is recorded in `document_source` and travels into the catalog
    entry, so a consumer can tell an extraction from a placeholder instead of
    receiving identical attributes for every document and no way to know.

    The text is truncated to the configured ceiling before anything downstream
    sees it, so one oversized document cannot set the size of the whole
    pipeline's work.

    Trust level: the outer entry gate is the trust boundary for the request;
    inner pipeline nodes run below it.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def _max_document_chars(self, state: Dict[str, Any]) -> int:
        extraction = from_json(state.get("extraction_tuning"), {})
        value = extraction.get("max_document_chars") if isinstance(extraction, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
        return _DEFAULT_MAX_DOCUMENT_CHARS

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        contract: Dict[str, Any] = from_json(state.get("caller_contract"), {}) or {}
        document_url = str(state.get("document_url", "") or contract.get("document_url", "") or "")
        document_type = str(state.get("document_type", "") or "PDF")
        caller_text = str(contract.get("document_text", "") or "")

        if not document_url and not caller_text:
            emit_trace_event("doc_parse_failed", {"reason": "no_document"}, state)
            return {
                "status": AgentStatus.ERROR,
                "error_log": ["DocParseNode: no document to parse — run InputSanitizeNode first"],
            }

        text, source = DocumentSource().resolve(
            caller_text=caller_text,
            document_url=document_url,
            document_type=document_type,
        )

        # Refused, not truncated. This entry carries a mandatory-allergen
        # compliance flag, and an allergen declared past a truncation point
        # would be silently absent from a document the entry then reports as
        # compliant. A refusal the caller can act on is the only safe answer
        # for a document larger than this deployment is configured to read.
        limit = self._max_document_chars(state)
        if len(text) > limit:
            emit_trace_event(
                "doc_parse_failed",
                {"reason": "document_exceeds_configured_ceiling", "limit": limit},
                state,
            )
            return {
                "status": AgentStatus.ERROR,
                "error_log": [
                    "DocParseNode: the document exceeds the configured ceiling of "
                    f"{limit} characters and was not processed"
                ],
            }

        emit_trace_event(
            "doc_parse_complete",
            {
                "document_type": document_type,
                "document_source": source,
                "raw_text_length": len(text),
            },
            state,
        )

        return {
            "raw_text": text,
            "document_source": source,
            "status": AgentStatus.SUCCESS,
        }
