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

# The mandatory allergen declarations under the food labelling rules. A closed
# list: the reported allergens are always a subset of it, which is one half of
# the output invariant the boundary enforces.
MANDATORY_ALLERGENS: frozenset[str] = frozenset(
    {
        "えび",
        "かに",
        "小麦",
        "そば",
        "卵",
        "乳",
        "落花生",
        "アーモンド",
        "あわび",
        "いか",
        "いくら",
        "オレンジ",
        "カシューナッツ",
        "キウイフルーツ",
        "牛肉",
        "くるみ",
        "ごま",
        "さけ",
        "さば",
        "大豆",
        "鶏肉",
        "バナナ",
        "豚肉",
        "まつたけ",
        "もも",
        "やまいも",
        "りんご",
        "ゼラチン",
    }
)

# The redaction sentinel the platform substitutes for personal-data shapes.
# A value carrying it is NOT an extracted value: the platform rewrote the text
# before this node could read it, and reporting the sentinel as the product
# name would certify a mask as a confident extraction.
_REDACTION_SENTINEL = "[MASKED]"

_DEFAULT_MAX_FIELD_CHARS = 200
_DEFAULT_MAX_ALLERGENS = 28

# Every label this document format uses. A field value ends at the next label
# or at the end of the line, whichever comes first — without the lookahead, a
# parser that returns the whole document on ONE line makes the first field
# swallow every field after it, and the product name comes back carrying the
# price, the ingredients and the certifications.
_LABELS = ("製品名", "商品名", "品名", "価格", "原材料", "原料", "成分", "アレルゲン", "認証")
_NEXT_LABEL = r"(?=\s*(?:" + "|".join(_LABELS) + r")[：:]|\n|$)"


def _field(text: str, labels: tuple[str, ...]) -> str:
    """Return the first value among `labels`, bounded at the next label."""
    for label in labels:
        match = re.search(label + r"[：:]\s*(.*?)" + _NEXT_LABEL, text)
        if match:
            value = match.group(1).strip().rstrip(",、")
            if value:
                return value
    return ""


class AttributeExtractAndAllergenCheckNode(FunctionNode):
    """Inner pipeline: product attributes and the mandatory allergen check.

    Produces, from the document text:
      - extracted_attributes: product name, price, ingredients and a bounded
        excerpt, each truncated to the configured field cap
      - extraction_status:    complete, partial, or redacted
      - detected_allergens:   the mandatory allergen names present in the text
      - allergen_compliance:  the document carries a usable declaration

    `extraction_status` is the honest half. The platform rewrites personal-data
    shapes out of the request string before any template code runs, and its
    name heuristic reads title-case proper nouns as personal names — which, on
    a retail specification, is exactly what a product or brand name looks like.
    So the extraction can be handed a redaction sentinel where the product name
    should be. When that happens the field is reported empty and the status is
    `redacted`; it is never reported as an extracted product name.

    Trust level: the outer entry gate is the trust boundary for the request;
    inner pipeline nodes run below it.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def _tuning(self, state: Dict[str, Any]) -> Dict[str, Any]:
        tuning = from_json(state.get("extraction_tuning"), {})
        return tuning if isinstance(tuning, dict) else {}

    @staticmethod
    def _positive_int(value: Any, fallback: int) -> int:
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
        return fallback

    def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        raw_text = state.get("raw_text", "")

        if not raw_text or not isinstance(raw_text, str):
            emit_trace_event(
                "attribute_extract_allergen_check_failed",
                {"reason": "missing_raw_text"},
                state,
            )
            return {
                "status": AgentStatus.ERROR,
                "error_log": ["AttributeExtractAndAllergenCheckNode: raw_text is missing " "— run DocParseNode first"],
            }

        tuning = self._tuning(state)
        max_field = self._positive_int(tuning.get("max_field_chars"), _DEFAULT_MAX_FIELD_CHARS)
        max_allergens = self._positive_int(tuning.get("max_allergens"), _DEFAULT_MAX_ALLERGENS)

        product_name = _field(raw_text, ("製品名", "商品名", "品名"))
        price = _field(raw_text, ("価格",))
        ingredients = _field(raw_text, ("原材料", "原料", "成分"))

        # A masked value is not an extracted value.
        redacted = _REDACTION_SENTINEL in product_name
        if redacted:
            product_name = ""
        if _REDACTION_SENTINEL in ingredients:
            ingredients = ""
            redacted = True

        if redacted:
            extraction_status = "redacted"
        elif product_name:
            extraction_status = "complete"
        else:
            extraction_status = "partial"

        detected: List[str] = sorted(allergen for allergen in MANDATORY_ALLERGENS if allergen in raw_text)[
            :max_allergens
        ]
        allergen_compliance = bool(detected) or "アレルゲン" in raw_text

        attributes = {
            "product_name": product_name[:max_field],
            "price": price[:max_field],
            "ingredients": ingredients[:max_field],
            "document_excerpt": raw_text[:max_field],
        }

        emit_trace_event(
            "attribute_extract_allergen_check_complete",
            {
                # Counts and status only: the extracted values are caller
                # content and do not belong in the audit record.
                "extraction_status": extraction_status,
                "detected_allergen_count": len(detected),
                "allergen_compliance": allergen_compliance,
            },
            state,
        )

        return {
            "extracted_attributes": to_json(attributes),
            "extraction_status": extraction_status,
            "detected_allergens": to_json(detected),
            "allergen_compliance": allergen_compliance,
            "status": AgentStatus.SUCCESS,
        }
