"""AgentCore Platform v1.0"""

# RET-C2-227 — outer graph.
#
# Retail supplier-document parse and catalog enrichment.
#
# Architecture:
#
#   Outer backbone (fixed — add_edges() is NOT overridden):
#     START -> initialize -> pre_process -> main -> {route} -> post_process
#           -> finalize -> END
#                            | (retry, bounded by max_retry)
#                            -> pre_process
#
#   The `main` slot is a GraphNode subclass (DocCatalogEnrichGraphNode) that
#   delegates the whole document pipeline to DomainWorkflowGraph, so the domain
#   topology stays inside the inner graph and the backbone is untouched.
#
# Directory layout:
#   src/graph/graph.py                 <- outer graph (this file)
#   src/graph/domain_workflow_graph.py <- inner graph (document pipeline)
#   src/graph/context_bridge.py        <- validated caller contract across the boundary
#
# Rules enforced here:
#   - RetailDocCatalogEnrichAgent inherits AgentBaseGraph (framework base class, directly)
#   - super().register_nodes() is called first (fills initialize + finalize)
#   - DocCatalogEnrichGraphNode occupies self._nodes["main"]
#   - merge_output() returns only changed keys
#   - add_edges() is NOT overridden
#   - No platform SDK imports

from pathlib import Path
from typing import Any, ClassVar, Dict

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from framework.utils.config_loader import load_agent_config
from src.graph.context_bridge import set_caller_contract
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State, from_json

# Repo root: src/graph/graph.py -> parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Mirrors config/config.yaml so the extraction bounds are never empty even
# where the config file is unreadable in an unusual deployment layout.
_FALLBACK_EXTRACTION: Dict[str, Any] = {
    "max_document_chars": 20000,
    "max_field_chars": 200,
    "max_allergens": 28,
    "max_certifications": 16,
}


def runtime_config() -> Dict[str, Any]:
    """Load config/config.yaml — the live runtime parameters.

    The registry loads this file and passes it to the graph constructor; the
    standalone HTTP entry point does the same, so max_retry and the extraction
    bounds are live in both deployments rather than declared and ignored.

    Reading the static manifest (config/agent.yaml) here instead would return
    nothing: the manifest carries identity and compile-time requirements only,
    and a reader pointed at it degrades silently to defaults.
    """
    loaded = load_agent_config(_REPO_ROOT)
    return dict(loaded) if isinstance(loaded, dict) else {}


class DocCatalogEnrichGraphNode(GraphNode):
    """The `main` slot: wraps the inner document-enrichment pipeline.

    Contracts:
      get_subgraph()    - instantiate DomainWorkflowGraph with the forwarded
                          runtime configuration
      extract_input()   - hand the request to the inner graph and stash the
                          validated caller contract on the bridge
      merge_output()    - map the inner result into the outer state delta
      error_strategy    - "propagate": re-raise inner errors (fail fast)

    Trust level: the entry gate is the outer pre-process node; this node runs
    inside that boundary. The subgraph boundary has a deliberate no-op gate.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS
    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = False

    def _parent_config(self) -> Dict[str, Any]:
        """Forward the live extraction bounds to the inner graph.

        Returns the tuning under config["configurable"] — never an empty dict.
        The inner graph republishes it into inner state via its initial-state
        hook, because node execute() methods take no config parameter and state
        seeding is the only route a configured value can travel into a node.
        """
        extraction = runtime_config().get("extraction")
        if not isinstance(extraction, dict) or not extraction:
            extraction = dict(_FALLBACK_EXTRACTION)
        return {"configurable": {"extraction": extraction}}

    def get_subgraph(self) -> Any:
        """Instantiate and return the inner document workflow graph.

        Imported inside the method to avoid circular-import risk at module load
        time. The inner graph receives the runtime-derived configuration through
        its constructor; its domain nodes still take no constructor arguments.
        """
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        return DomainWorkflowGraph(config=self._parent_config())

    def extract_input(self, state: AgentState) -> str:
        """Return the request string, and bridge the validated caller contract.

        The framework hands only a string to the inner graph, so the structured
        part of the request travels on the bridge instead — set here, one step
        before the inner invoke, and read by the inner graph's initial-state
        hook. Only the contract the pre-process node already validated crosses.
        """
        set_caller_contract(from_json(state.get("caller_contract"), {}) or {})
        return str(state.get("validated_input") or state.get("user_input", ""))

    def merge_output(self, state: AgentState, sub_result: Dict[str, Any]) -> Dict[str, Any]:
        """Map the inner result into the outer state delta (changed keys only).

        Key coupling, designed together with DomainWorkflowGraph.get_output():
          inner get_output() emits  -> "catalog_entry", "status"
          this merge_output() reads -> sub_result.get("catalog_entry"),
                                       sub_result.get("status")

        The entry is mapped to "result" as well, because the outer post-process
        node reads state["result"] — without that mapping the published output
        would always be empty.

        The status guard below is DEFENSIVE, not live. A non-success inner
        status never reaches here while error_strategy is "propagate" — the
        inner error is re-raised first — so removing the guard leaves the whole
        suite green, and its unit test calls this method directly rather than
        pretending the path is exercised end to end.

        It stays because what it guards is one configuration change away:
        switching the strategy to "handle" would start mapping a partially
        assembled entry into "result", and the framework's envelope falls back
        to "result" with no status check of its own.
        """
        status = sub_result.get("status")
        status_value = status.value if isinstance(status, AgentStatus) else status
        entry = sub_result.get("catalog_entry")
        if status_value != AgentStatus.SUCCESS.value:
            return {"catalog_entry": "", "result": None, "status": status}
        return {"catalog_entry": entry, "result": entry, "status": status}


class RetailDocCatalogEnrichAgent(AgentBaseGraph):
    """Outer graph for RET-C2-227.

    Inherits AgentBaseGraph directly (framework base class). The domain logic is
    fully encapsulated in DocCatalogEnrichGraphNode (the `main` slot), which
    delegates to DomainWorkflowGraph.

    Backbone (fixed):
        START -> initialize -> pre_process -> main -> post_process -> finalize -> END

    register_nodes() is the only structural override:
      - super().register_nodes() fills initialize and finalize
      - pre_process:  PreProcessNode  (trust gate, contract validation, screens)
      - main:         DocCatalogEnrichGraphNode
      - post_process: PostProcessNode (publish boundary)

    add_edges() is NOT overridden — backbone wiring belongs to the framework.
    """

    @property
    def name(self) -> str:
        return "RetailDocCatalogEnrichAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        """Fill all five backbone slots.

        super().register_nodes() MUST be called first — it injects the
        framework's default initialize node (schema version, session id, trust
        level) and finalize node (response metadata, total time).
        """
        super().register_nodes()  # fills: initialize, finalize
        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = DocCatalogEnrichGraphNode()
        self._nodes["post_process"] = PostProcessNode()

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.

    def get_output(self, state: AgentState) -> Dict[str, Any]:
        """Shape the caller-facing envelope.

        The framework's envelope resolves the output as
        ``formatted_output or result`` with NO status check, so any error path
        that left ``result`` in place ships the ungated entry inside the error
        envelope. Two of those paths cannot be closed by the publish boundary
        itself: an error routes straight to finalize, so the publish node never
        runs at all; and when the framework's own scan raises on a node's return
        value, that node's whole delta is discarded — its clearing included —
        and ``result`` survives untouched in state.

        So on any non-success status the output resolves to the publish node's
        own notice, or to None. It never falls back to ``result``.

        The success path is deliberately NOT re-scanned here. A third scan would
        contain a content leak by itself and make the inner boundary's
        enforcement unfalsifiable; that boundary is the single place the
        success path is checked, and the mutant that removes it must be able to
        fail.
        """
        output: Dict[str, Any] = dict(super().get_output(state))
        status = state.get("status")
        status_value = status.value if isinstance(status, AgentStatus) else status
        if status_value != AgentStatus.SUCCESS.value:
            output["output"] = state.get("formatted_output") or None
        return output


# The manifest resolves the entry point by the class name above; this alias
# keeps the generic name importable as well.
Graph = RetailDocCatalogEnrichAgent
