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
from src.nodes.attribute_extract_and_allergen_check_node import MANDATORY_ALLERGENS
from src.nodes.cert_validate_node import KNOWN_CERTIFICATIONS
from src.schemas.state import from_json
from src.services.screens import credential_findings

# ── The published output invariant ───────────────────────────────────────────
#
# This template renders no monetary aggregate: `price` is a label value copied
# from the supplier document, and rounding it would replace the supplier's own
# figure with one they never quoted. So the fleet's rounding grid does not
# apply here, and the boundary enforces THIS template's stated invariant
# instead — the one its documented schema actually claims:
#
#   1. the entry carries exactly the declared keys, no more and no fewer
#   2. every reported allergen is one of the mandatory declarations
#   3. every reported certification is one of the recognised marks
#   4. every rendered string is within the configured field cap
#   5. the supplier code is an inert identifier
#   6. the document source and extraction status are closed-set labels
#   7. no credential pattern appears anywhere in the entry
#
# Enforced here rather than assumed upstream: an invariant that is only
# maintained by the code that builds the value is not an invariant, it is a
# convention. Items 2 and 3 are the ones that matter most to a consumer —
# "allergens" is a compliance field, and a value outside the mandatory list
# would be read as a declaration the labelling rules do not recognise.

CATALOG_KEYS: frozenset[str] = frozenset(
    {
        "product_name",
        "price",
        "ingredients",
        "document_excerpt",
        "extraction_status",
        "allergens",
        "allergen_compliance",
        "certifications_claimed",
        "cert_claim_present",
        "document_url",
        "document_type",
        "document_source",
        "supplier_code",
        "compliance_flags",
    }
)

_EXTRACTION_STATUSES: frozenset[str] = frozenset({"complete", "partial", "redacted"})
_DOCUMENT_SOURCES: frozenset[str] = frozenset({"caller", "parser", "sample"})
_SUPPLIER_CODE_RE = re.compile(r"^[a-z0-9_]{0,32}$")

_DEFAULT_MAX_FIELD_CHARS = 200


class OutputInvariantError(ValueError):
    """The assembled entry violates the published invariant.

    Carries a closed-set reason label only. The offending value is never
    included: this message reaches the error log, and an error log is not a
    channel for content the gate just refused to release.
    """


def enforce_output_invariant(entry: Dict[str, Any], max_field_chars: int) -> None:
    """Raise OutputInvariantError unless `entry` satisfies the published invariant."""
    keys = set(entry)
    if keys != set(CATALOG_KEYS):
        raise OutputInvariantError("catalog_entry key set does not match the published schema")

    allergens = entry.get("allergens")
    if not isinstance(allergens, list) or any(a not in MANDATORY_ALLERGENS for a in allergens):
        raise OutputInvariantError("allergens contains a value outside the mandatory declarations")

    certifications = entry.get("certifications_claimed")
    if not isinstance(certifications, list) or any(c not in KNOWN_CERTIFICATIONS for c in certifications):
        raise OutputInvariantError("certifications_claimed contains a value outside the recognised marks")

    for field in ("product_name", "price", "ingredients", "document_excerpt"):
        value = entry.get(field)
        if not isinstance(value, str):
            raise OutputInvariantError(f"{field} is not a string")
        if len(value) > max_field_chars:
            raise OutputInvariantError(f"{field} exceeds the configured field cap")

    if entry.get("extraction_status") not in _EXTRACTION_STATUSES:
        raise OutputInvariantError("extraction_status is not a recognised label")
    if entry.get("document_source") not in _DOCUMENT_SOURCES:
        raise OutputInvariantError("document_source is not a recognised label")

    supplier_code = entry.get("supplier_code")
    if not isinstance(supplier_code, str) or not _SUPPLIER_CODE_RE.match(supplier_code):
        raise OutputInvariantError("supplier_code is not an inert identifier")

    findings = credential_findings(entry)
    if findings:
        raise OutputInvariantError("catalog_entry carries a credential-shaped value (" + ", ".join(findings) + ")")


class OutputStructureAndOutputValidateNode(FunctionNode):
    """Inner pipeline: assemble the catalog entry and enforce its invariant.

    On a violation the node returns ERROR and CLEARS the output-bearing field
    rather than raising. Raising is not containment: the framework's envelope
    resolves the caller-visible output as the formatted output OR the raw
    result with no status check, so an error path that leaves the entry in
    state ships the ungated entry inside the error envelope. Returning ERROR
    with `catalog_entry` explicitly emptied is what removes it from every path
    that could surface it.

    Trust level: the outer entry gate is the trust boundary for the request;
    inner pipeline nodes run below it.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def _max_field_chars(self, state: Dict[str, Any]) -> int:
        tuning: Any = from_json(state.get("extraction_tuning"), {})
        value = tuning.get("max_field_chars") if isinstance(tuning, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
        return _DEFAULT_MAX_FIELD_CHARS

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        attributes: Any = from_json(state.get("extracted_attributes"), {})
        if not isinstance(attributes, dict):
            attributes = {}
        allergens: Any = from_json(state.get("detected_allergens"), [])
        if not isinstance(allergens, list):
            allergens = []
        certifications: Any = from_json(state.get("certifications"), [])
        if not isinstance(certifications, list):
            certifications = []

        allergen_compliance = bool(state.get("allergen_compliance", False))
        cert_claim_present = bool(state.get("cert_compliance", False))
        max_field = self._max_field_chars(state)

        entry: Dict[str, Any] = {
            "product_name": str(attributes.get("product_name", ""))[:max_field],
            "price": str(attributes.get("price", ""))[:max_field],
            "ingredients": str(attributes.get("ingredients", ""))[:max_field],
            "document_excerpt": str(attributes.get("document_excerpt", ""))[:max_field],
            "extraction_status": str(state.get("extraction_status", "partial")),
            "allergens": [a for a in allergens if isinstance(a, str)],
            "allergen_compliance": allergen_compliance,
            # Named for what the check establishes: the mark is CLAIMED in the
            # document. No registry lookup is performed, so calling this
            # "cert_valid" would assert something the template never checked.
            "certifications_claimed": [c for c in certifications if isinstance(c, str)],
            "cert_claim_present": cert_claim_present,
            "document_url": str(state.get("document_url", "")),
            "document_type": str(state.get("document_type", "")),
            "document_source": str(state.get("document_source", "sample")),
            "supplier_code": str(state.get("supplier_code", "") or ""),
            "compliance_flags": {
                "mandatory_allergen_declaration_present": allergen_compliance,
                "certification_mark_claimed": cert_claim_present,
            },
        }

        try:
            enforce_output_invariant(entry, max_field)
        except OutputInvariantError as exc:
            emit_trace_event(
                "output_invariant_violation",
                {"reason": str(exc)},
                state,
            )
            return {
                # Clear the output-bearing field on the same return that reports
                # the error, so no path can surface a partially assembled entry.
                "catalog_entry": "",
                "status": AgentStatus.ERROR,
                "error_log": [f"OutputStructureAndOutputValidateNode: {exc}"],
            }

        catalog_json = json.dumps(entry, ensure_ascii=False)

        emit_trace_event(
            "output_structure_validate_complete",
            {
                "extraction_status": entry["extraction_status"],
                "document_source": entry["document_source"],
                "allergen_count": len(entry["allergens"]),
                "certification_count": len(entry["certifications_claimed"]),
            },
            state,
        )

        return {
            "catalog_entry": catalog_json,
            "status": AgentStatus.SUCCESS,
        }
