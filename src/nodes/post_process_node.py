"""AgentCore Platform v1.0"""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes, never the full state
#  - Return AgentStatus enum constants, never plain strings
#  - Never import from mediator/, api/, or other agents

import json
from typing import Any, ClassVar, Dict

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.nodes.output_structure_and_output_validate_node import CATALOG_KEYS

# Published in place of the entry when the boundary refuses to release it.
# Deliberately truthy: the framework's envelope resolves the caller-visible
# output as the formatted output OR the raw result, so an empty string here
# would re-open exactly the fallback this node exists to close.
WITHHELD_NOTICE = "Catalog entry withheld: the output boundary did not release it."


class PostProcessNode(FunctionNode):
    """Outer backbone: publish the catalog entry the inner boundary approved.

    This node publishes; it does not re-inspect the entry's CONTENT. The
    content invariant — closed allergen and certification sets, field caps, the
    inert supplier code, no credential-shaped value — is enforced once, inside
    the inner graph, by the node that assembles the entry. Re-scanning it here
    would contain a content leak on its own and thereby make that enforcement
    unfalsifiable: the test that proves it would pass with the inner check
    deleted. More defence would buy less assurance.

    What this node owns is the hand-off, which the inner graph cannot reach:

    - it verifies the value arriving as `result` is STRUCTURALLY the approved
      entry — decodable JSON carrying exactly the published key set — so a
      mis-mapped or partially assembled value cannot be published as one;
    - it publishes only on success, and on anything else it clears every
      output-bearing field and publishes the withheld notice instead.

    Trust level: the entry gate on the request already ran; this node is inside
    that boundary.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def _withhold(self, state: Dict[str, Any], reason: str) -> Dict[str, Any]:
        """Clear every output-bearing field and publish the notice instead."""
        emit_trace_event("post_process_output_withheld", {"reason": reason}, state)
        return {
            "formatted_output": WITHHELD_NOTICE,
            "result": None,
            "catalog_entry": "",
            "status": AgentStatus.ERROR,
            "error_log": [f"PostProcessNode: catalog entry withheld ({reason})"],
        }

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        status = state.get("status")
        status_value = status.value if isinstance(status, AgentStatus) else status
        if status_value != AgentStatus.SUCCESS.value:
            return self._withhold(state, "upstream_status_not_success")

        result = state.get("result")
        if not isinstance(result, str) or not result:
            return self._withhold(state, "no_entry_to_publish")

        try:
            decoded: Any = json.loads(result)
        except (json.JSONDecodeError, ValueError):
            return self._withhold(state, "entry_not_decodable")

        if not isinstance(decoded, dict) or set(decoded) != set(CATALOG_KEYS):
            return self._withhold(state, "entry_shape_unrecognised")

        emit_trace_event(
            "post_process_output_published",
            {
                "output_length": len(result),
                "extraction_status": str(decoded.get("extraction_status", "")),
                "document_source": str(decoded.get("document_source", "")),
            },
            state,
        )

        return {
            "formatted_output": result,
            "status": AgentStatus.SUCCESS,
        }
