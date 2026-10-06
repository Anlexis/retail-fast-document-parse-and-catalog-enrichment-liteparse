"""AgentCore Platform v1.0"""

# State is a flat TypedDict, never a Pydantic model: LangGraph checkpoints use
# msgpack serialization and model instances do not survive it intact. Extend
# AgentState with agent-specific fields only, and keep every value a primitive
# or a JSON string. No credentials or secrets are ever placed in state.

import json
from typing import Any, Optional

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """RET-C2-227 retail document parse and catalog enrichment state.

    Every inherited field (user_input, status, session_id, node_history,
    error_log, validated_input, formatted_output, ...) comes from AgentState.
    Only domain fields are declared here.

    Caller contract and runtime bounds, both carried as JSON strings so state
    stays flat and checkpoint-safe:
    - caller_contract:      the validated structured parameters
    - extraction_tuning:    the configured extraction bounds, seeded into the
                            inner graph because node execute() takes no config

    Document reference and provenance:
    - document_url:         reference to the supplier document
    - document_type:        PDF | IMAGE | EXCEL | CSV
    - supplier_code:        inert supplier identifier, rendered into the entry
    - document_source:      caller | parser | sample — which branch produced
                            the text this entry was extracted from

    Pipeline fields:
    - raw_text:             the document text the extraction ran on
    - extracted_attributes: JSON object of product attributes
    - extraction_status:    complete | partial | redacted — never "complete"
                            for a value the platform redacted before the
                            extraction could read it
    - detected_allergens:   JSON list of matched mandatory allergen names
    - allergen_compliance:  the document carries a usable allergen declaration
    - certifications:       JSON list of recognised certification names
    - cert_compliance:      at least one recognised certification is present

    Final output:
    - catalog_entry:        the structured JSON catalog entry
    """

    # Caller contract and the runtime bounds seeded into the inner graph
    caller_contract: Optional[str]
    extraction_tuning: Optional[str]

    # Document reference and provenance
    document_url: Optional[str]
    document_type: Optional[str]
    supplier_code: Optional[str]
    document_source: Optional[str]

    # Pipeline
    raw_text: Optional[str]
    extracted_attributes: Optional[str]
    extraction_status: Optional[str]
    detected_allergens: Optional[str]
    allergen_compliance: Optional[bool]
    certifications: Optional[str]
    cert_compliance: Optional[bool]

    # Final assembled catalog output
    catalog_entry: Optional[str]


def to_json(value: Any) -> str:
    """Serialize a contract or pipeline value for carriage in flat state."""
    return json.dumps(value, ensure_ascii=False)


def from_json(value: Any, default: Any) -> Any:
    """Deserialize a state-carried JSON string, falling back to `default`.

    Returns `default` for anything that is not a decodable JSON string, so a
    node that reads a field a prior node never wrote degrades to its declared
    default rather than raising mid-pipeline.
    """
    if not isinstance(value, str) or not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return default
