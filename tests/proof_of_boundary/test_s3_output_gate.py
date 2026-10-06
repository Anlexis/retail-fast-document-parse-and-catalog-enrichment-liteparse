# PB: the output boundary, and what the caller-visible envelope carries.
#
# The envelope resolves the caller-visible output as the formatted output OR
# the raw result, with NO status check of its own. So "the gate raised" is not
# the same as "nothing was released": an error path that leaves the assembled
# entry in state ships it inside the error envelope, credential and all.
#
# Three places enforce that, and each guards something the others cannot:
#
#   - the inner boundary enforces the entry's CONTENT invariant and clears the
#     entry on a violation. Nothing else checks the closed allergen and
#     certification sets, the field caps or the inert supplier code.
#   - the publish node owns the hand-off. It publishes only on success, and on
#     anything else clears the result and publishes a truthy notice. The inner
#     graph cannot reach the outer result field at all.
#   - the envelope override closes the two paths neither of the above can
#     reach: an error routes straight to finalize, so the publish node never
#     runs; and when the framework's own scan raises on a node's return value,
#     that node's whole delta — its clearing included — is discarded.
#
# The success path is checked in exactly ONE place on purpose. A second scan
# there would contain a leak by itself and make the first unfalsifiable.

import json

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from src.graph.graph import RetailDocCatalogEnrichAgent
from src.nodes.output_structure_and_output_validate_node import (
    OutputInvariantError,
    OutputStructureAndOutputValidateNode,
    enforce_output_invariant,
)
from src.nodes.post_process_node import WITHHELD_NOTICE
from src.schemas.state import to_json

CLEAN_DOCUMENT = "\n".join(("製品名: 有機そば粉クッキー", "価格: 850円", "原材料: そば, 小麦, 卵", "認証: 有機JAS"))

# Assignment shapes the framework's format-based detector does not describe, so
# the template's own patterns are the only thing standing between them and the
# published entry. That makes them the right probe for this boundary.
LOCAL_ONLY_CREDENTIALS = ["password=hunter2xyz", "api_key=ABCDEFGH12345678"]

FRAMEWORK_CREDENTIALS = ["AKIAIOSFODNN7EXAMPLE", "postgresql://user:example_password@db.example/catalog"]


def _state(**kwargs) -> dict:
    state = {
        "caller_trust_level": TrustLevel.ANONYMOUS.value,
        "correlation_id": "pb-output-gate",
        "node_history": [],
        "error_log": [],
        "extraction_tuning": to_json({"max_field_chars": 200}),
        "extracted_attributes": to_json(
            {
                "product_name": "有機そば粉クッキー",
                "price": "850円",
                "ingredients": "そば, 小麦, 卵",
                "document_excerpt": CLEAN_DOCUMENT,
            }
        ),
        "extraction_status": "complete",
        "detected_allergens": to_json(["そば", "卵", "小麦"]),
        "allergen_compliance": True,
        "certifications": to_json(["有機JAS"]),
        "cert_compliance": True,
        "document_url": "https://supplier.example/a.pdf",
        "document_type": "PDF",
        "document_source": "caller",
        "supplier_code": "supplier_00417",
    }
    state.update(kwargs)
    return state


@pytest.fixture(autouse=True)
def silence_domain_audit(monkeypatch):
    for module in (
        "src.nodes.pre_process_node",
        "src.nodes.post_process_node",
        "src.nodes.input_sanitize_node",
        "src.nodes.doc_parse_node",
        "src.nodes.attribute_extract_and_allergen_check_node",
        "src.nodes.cert_validate_node",
        "src.nodes.output_structure_and_output_validate_node",
    ):
        monkeypatch.setattr(module + ".emit_trace_event", lambda *a, **k: None)


class TestTheBoundaryNode:
    node = OutputStructureAndOutputValidateNode()

    def test_a_clean_entry_is_released(self):
        result = self.node.execute(_state())
        assert result["status"] == AgentStatus.SUCCESS
        assert json.loads(result["catalog_entry"])["product_name"] == "有機そば粉クッキー"

    @pytest.mark.parametrize("leak", LOCAL_ONLY_CREDENTIALS + FRAMEWORK_CREDENTIALS)
    def test_a_credential_bearing_entry_is_withheld_and_cleared(self, leak):
        state = _state(
            extracted_attributes=to_json(
                {
                    "product_name": "x",
                    "price": "",
                    "ingredients": "",
                    "document_excerpt": f"備考: {leak}",
                }
            )
        )
        result = self.node.execute(state)
        assert result["status"] == AgentStatus.ERROR
        assert result["catalog_entry"] == ""
        assert leak not in json.dumps(result, ensure_ascii=False)

    def test_the_invariant_covers_more_than_credentials(self):
        """The framework's scan checks credentials and nothing else.

        The closed allergen and certification sets, the field caps and the
        inert supplier code are this template's own claims, and this is the
        only place they are enforced.
        """
        for bad in (
            {"allergens": ["うるし"]},
            {"certifications_claimed": ["MADE-UP-MARK"]},
            {"supplier_code": "Supplier 0417!"},
            {"product_name": "x" * 201},
            {"extraction_status": "definitely"},
        ):
            entry = json.loads(self.node.execute(_state())["catalog_entry"])
            entry.update(bad)
            with pytest.raises(OutputInvariantError):
                enforce_output_invariant(entry, 200)


class TestTheCallerVisibleEnvelope:
    """Driven through the real compiled graph, not at node level."""

    def _invoke(self, document_text: str) -> dict:
        agent = RetailDocCatalogEnrichAgent()
        agent.compile()
        ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        return agent.invoke("enrich", ctx=ctx, input_context={"document_text": document_text})

    def test_a_clean_request_publishes_the_entry(self):
        result = self._invoke(CLEAN_DOCUMENT)
        assert result["status"] == AgentStatus.SUCCESS.value
        assert json.loads(result["output"])["product_name"] == "有機そば粉クッキー"

    @pytest.mark.parametrize("leak", LOCAL_ONLY_CREDENTIALS)
    def test_the_error_envelope_releases_nothing(self, leak):
        result = self._invoke(f"{CLEAN_DOCUMENT}\n備考: {leak}")
        assert result["status"] != AgentStatus.SUCCESS.value
        blob = json.dumps(result, ensure_ascii=False, default=str)
        for forbidden in (leak, "Traceback", "/Users/", "src/nodes/", "有機そば粉クッキー"):
            assert forbidden not in blob, f"error envelope leaked: {forbidden}"
        # The envelope resolves to the publish notice or to nothing — never to
        # the raw result.
        assert result["output"] in (None, WITHHELD_NOTICE)

    def test_the_envelope_never_falls_back_to_result_on_a_non_success_status(self):
        """Directly: put an entry in `result` and a non-success status in state."""
        agent = RetailDocCatalogEnrichAgent()
        entry = json.dumps({"product_name": "leaked", "password": "hunter2xyz"})
        output = agent.get_output({"status": AgentStatus.ERROR.value, "result": entry, "formatted_output": None})
        assert output["output"] is None
        assert "hunter2xyz" not in json.dumps(output)
