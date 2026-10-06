"""AgentCore Platform v1.0"""

# Caller-contract bridge across the outer/inner graph boundary.
#
# Why it exists: the framework invokes a nested graph as
# `subgraph.invoke(user_input, session_id=..., ctx=...)`. Only the request
# STRING crosses. Neither the outer state nor the caller's structured
# invocation parameters are forwarded, so the validated contract the outer
# pre-process node produced would never reach the document pipeline — the
# inner nodes would see an empty context on every call and the agent would
# answer from the illustrative document no matter what the caller sent.
#
# Two sanctioned subclass hooks bridge it, in this order:
#
#   DocCatalogEnrichGraphNode.extract_input(state)   [BEFORE subgraph.invoke]
#       -> set_caller_contract(<validated contract>)
#   DomainWorkflowGraph._extra_initial_state()       [INSIDE subgraph.invoke]
#       -> seeds the contract into the inner state
#
# Only the VALIDATED contract crosses: every field has already passed its
# length, alphabet and enum check, and the raw request body never travels.
#
# A ContextVar rather than a module global, so the hand-off is correct per
# thread and per task and concurrent invocations inside one process cannot see
# each other's contract.

from contextvars import ContextVar
from typing import Any, Dict, Optional

_CALLER_CONTRACT: ContextVar[Optional[Dict[str, Any]]] = ContextVar("ret_c2_227_caller_contract", default=None)


def set_caller_contract(contract: Optional[Dict[str, Any]]) -> None:
    """Stash the validated caller contract for the imminent inner-graph invoke."""
    _CALLER_CONTRACT.set(dict(contract) if contract else {})


def get_caller_contract() -> Dict[str, Any]:
    """Read (without consuming) the stashed contract; {} when none was set."""
    return _CALLER_CONTRACT.get() or {}
