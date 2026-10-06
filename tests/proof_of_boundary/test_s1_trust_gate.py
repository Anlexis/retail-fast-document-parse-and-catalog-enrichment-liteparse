# PB-S1: Trust Level Gate Verification (RET-C2-227)
#
# Verifies that:
#   - PreProcessNode (outer backbone) declares VERIFIED_EXTERNAL — rejects ANONYMOUS callers
#   - All Cat-2 inner domain nodes declare ANONYMOUS (correct per review finding 5 / ADR-017)
#   - PostProcessNode declares ANONYMOUS
#   - DocCatalogEnrichGraphNode (main-slot GraphNode) declares ANONYMOUS
#
# The outer backbone (PreProcessNode) is the only trust gate facing external callers.
# Inner nodes must be ANONYMOUS so they are not denied by the framework when
# called via the inner graph's InvocationContext (which carries VERIFIED_EXTERNAL).

import pytest

from framework.schemas.trust_level import TrustLevel


class TestS1TrustGateDeclarations:
    """PB-S1-A: Trust level declarations on all node classes."""

    def test_pre_process_requires_verified_external(self):
        """TC-S1-01: PreProcessNode must declare VERIFIED_EXTERNAL (outer S-1 gate)."""
        from src.nodes.pre_process_node import PreProcessNode

        assert PreProcessNode.required_trust_level == TrustLevel.VERIFIED_EXTERNAL

    def test_post_process_is_anonymous(self):
        """TC-S1-02: PostProcessNode must declare ANONYMOUS."""
        from src.nodes.post_process_node import PostProcessNode

        assert PostProcessNode.required_trust_level == TrustLevel.ANONYMOUS

    def test_main_slot_graph_node_is_anonymous(self):
        """TC-S1-03: DocCatalogEnrichGraphNode (main slot) must declare ANONYMOUS."""
        from src.graph.graph import DocCatalogEnrichGraphNode

        assert DocCatalogEnrichGraphNode.required_trust_level == TrustLevel.ANONYMOUS

    def test_input_sanitize_node_is_anonymous(self):
        """TC-S1-04: InputSanitizeNode must declare ANONYMOUS."""
        from src.nodes.input_sanitize_node import InputSanitizeNode

        assert InputSanitizeNode.required_trust_level == TrustLevel.ANONYMOUS

    def test_doc_parse_node_is_anonymous(self):
        """TC-S1-05: DocParseNode must declare ANONYMOUS."""
        from src.nodes.doc_parse_node import DocParseNode

        assert DocParseNode.required_trust_level == TrustLevel.ANONYMOUS

    def test_attribute_extract_allergen_check_node_is_anonymous(self):
        """TC-S1-06: AttributeExtractAndAllergenCheckNode must declare ANONYMOUS."""
        from src.nodes.attribute_extract_and_allergen_check_node import (
            AttributeExtractAndAllergenCheckNode,
        )

        assert AttributeExtractAndAllergenCheckNode.required_trust_level == TrustLevel.ANONYMOUS

    def test_cert_validate_node_is_anonymous(self):
        """TC-S1-07: CertValidateNode must declare ANONYMOUS."""
        from src.nodes.cert_validate_node import CertValidateNode

        assert CertValidateNode.required_trust_level == TrustLevel.ANONYMOUS

    def test_output_structure_validate_node_is_anonymous(self):
        """TC-S1-08: OutputStructureAndOutputValidateNode must declare ANONYMOUS."""
        from src.nodes.output_structure_and_output_validate_node import (
            OutputStructureAndOutputValidateNode,
        )

        assert OutputStructureAndOutputValidateNode.required_trust_level == TrustLevel.ANONYMOUS


class TestS1TrustGateBehaviour:
    """PB-S1-B: Trust gate enforcement via PreProcessNode.__call__()."""

    @pytest.fixture(autouse=True)
    def patch_emit(self, monkeypatch):
        monkeypatch.setattr("src.nodes.pre_process_node.emit_trace_event", lambda *a, **k: None)

    def test_anonymous_caller_rejected(self):
        """TC-S1-09: ANONYMOUS caller is rejected by PreProcessNode.__call__() trust gate."""
        from src.nodes.pre_process_node import PreProcessNode
        from framework.schemas.agent_status import AgentStatus

        node = PreProcessNode()
        state = {
            "user_input": '{"document_url": "https://example.com/doc.pdf"}',
            "caller_trust_level": TrustLevel.ANONYMOUS.value,
            "correlation_id": "s1-test",
            "node_history": [],
            "error_log": [],
        }
        result = node(state)  # __call__ enforces trust gate before execute()
        # ANONYMOUS (0) < VERIFIED_EXTERNAL (1) — trust gate must deny
        assert result.get("status") in (
            AgentStatus.ERROR,
            AgentStatus.ERROR.value,
        ), "ANONYMOUS caller should be denied by VERIFIED_EXTERNAL trust gate"

    def test_verified_external_caller_admitted(self):
        """TC-S1-10: VERIFIED_EXTERNAL caller passes through PreProcessNode trust gate."""
        from src.nodes.pre_process_node import PreProcessNode
        from framework.schemas.agent_status import AgentStatus

        node = PreProcessNode()
        state = {
            "user_input": '{"document_url": "https://example.com/doc.pdf"}',
            "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
            "correlation_id": "s1-test",
            "node_history": [],
            "error_log": [],
        }
        result = node(state)
        assert (
            result.get("status") == AgentStatus.SUCCESS
        ), f"VERIFIED_EXTERNAL caller should pass trust gate; got {result.get('status')!r}"
