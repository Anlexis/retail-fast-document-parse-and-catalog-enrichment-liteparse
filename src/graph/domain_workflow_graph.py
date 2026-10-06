"""AgentCore Platform v1.0"""

# Inner domain workflow graph for RET-C2-227.
# Instantiated by DocCatalogEnrichGraphNode.get_subgraph() in graph.py.
#
# Architecture:
#   Inherits BaseGraph (fully custom node topology — no forced backbone).
#   Linear pipeline:
#     START -> input_sanitize -> doc_parse -> attribute_extract_allergen_check
#           -> cert_validate -> output_structure_validate -> END
#
# Rules:
#   - Lives at src/graph/domain_workflow_graph.py
#   - Inherits BaseGraph (custom topology — no pre_process/main/post_process slots)
#   - Implements every BaseGraph abstract method
#   - register_nodes() does NOT call super() (abstract in BaseGraph)
#   - All nodes instantiated with NO constructor arguments
#   - Every inner node declares its required trust level explicitly
#   - initialize / finalize are NOT registered (outer backbone concern)

from typing import Any, Dict

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_status import AgentStatus
from src.graph.context_bridge import get_caller_contract
from src.nodes.attribute_extract_and_allergen_check_node import (
    AttributeExtractAndAllergenCheckNode,
)
from src.nodes.cert_validate_node import CertValidateNode
from src.nodes.doc_parse_node import DocParseNode
from src.nodes.input_sanitize_node import InputSanitizeNode
from src.nodes.output_structure_and_output_validate_node import (
    OutputStructureAndOutputValidateNode,
)
from src.schemas.state import State, to_json


class DomainWorkflowGraph(BaseGraph):
    """Inner graph for the RET-C2-227 document-enrichment pipeline.

    Drives the document processing flow:
        START -> input_sanitize -> doc_parse -> attribute_extract_allergen_check
              -> cert_validate -> output_structure_validate -> END

    Called by DocCatalogEnrichGraphNode.get_subgraph() in graph.py.
    get_output() feeds DocCatalogEnrichGraphNode.merge_output().
    """

    # ── Identity ─────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "RetailDocCatalogWorkflow"

    @property
    def state_schema(self) -> type:
        return State

    # ── Config validation ────────────────────────────────────────────────────

    def _validate_config(self) -> None:
        """Reject an extraction block whose bounds are not usable.

        The bounds are forwarded from the outer graph, which sources them from
        config/config.yaml. Validating them here means a bad value fails at
        compile time with the offending key named, rather than degrading to a
        default at request time and quietly changing what the agent returns.
        """
        extraction = (self.config.get("configurable") or {}).get("extraction")
        if extraction is None:
            return
        if not isinstance(extraction, dict):
            raise ValueError("extraction config must be a mapping")
        for key in ("max_document_chars", "max_field_chars", "max_allergens", "max_certifications"):
            if key not in extraction:
                continue
            value = extraction[key]
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"extraction.{key} must be a positive integer")

    # ── Initial-state hook ───────────────────────────────────────────────────

    def _extra_initial_state(self) -> Dict[str, Any]:
        """Seed the caller contract and the extraction bounds into inner state.

        The framework hands a nested graph only the request string, and node
        execute() methods take no config argument, so this hook is the single
        route by which the validated contract and the configured bounds reach
        the domain nodes. Both are carried as JSON strings, keeping state flat
        and checkpoint-safe.
        """
        extraction = (self.config.get("configurable") or {}).get("extraction") or {}
        return {
            "caller_contract": to_json(get_caller_contract()),
            "extraction_tuning": to_json(extraction),
        }

    # ── Node registration ────────────────────────────────────────────────────

    def register_nodes(self) -> None:
        """Register the inner domain nodes with no constructor arguments.

        No super() call — BaseGraph.register_nodes() is abstract. Every node in
        self._nodes must be referenced in add_edges().
        """
        self._nodes["input_sanitize"] = InputSanitizeNode()
        self._nodes["doc_parse"] = DocParseNode()
        self._nodes["attribute_extract_allergen_check"] = AttributeExtractAndAllergenCheckNode()
        self._nodes["cert_validate"] = CertValidateNode()
        self._nodes["output_structure_validate"] = OutputStructureAndOutputValidateNode()

    # ── Edge wiring ──────────────────────────────────────────────────────────

    def add_edges(self) -> None:
        """Wire the linear document enrichment pipeline."""
        self._sg.add_edge(START, "input_sanitize")
        self._sg.add_edge("input_sanitize", "doc_parse")
        self._sg.add_edge("doc_parse", "attribute_extract_allergen_check")
        self._sg.add_edge("attribute_extract_allergen_check", "cert_validate")
        self._sg.add_edge("cert_validate", "output_structure_validate")
        self._sg.add_edge("output_structure_validate", END)

    # ── Routing ──────────────────────────────────────────────────────────────

    def route(self, state: State) -> str:
        """Conditional routing — required by the BaseGraph contract.

        The topology above is linear, so add_edges() never references this.
        It is annotated with this graph's OWN State rather than the framework
        base state: the graph runtime reads a path callable's annotation as its
        input schema and projects away every field the annotation does not
        declare, so a base-state annotation would hide the domain fields a
        future conditional branch would route on.
        """
        return END if state.get("status") == AgentStatus.ERROR.value else "output_structure_validate"

    # ── Output shape ─────────────────────────────────────────────────────────

    def get_output(self, state: State) -> Dict[str, Any]:
        """Shape the sub_result handed to DocCatalogEnrichGraphNode.merge_output().

        Emits the entry under its own name rather than a generic "output" key,
        so the coupling with merge_output() is visible from both sides.
        """
        return {
            "catalog_entry": state.get("catalog_entry"),
            "status": state.get("status"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
