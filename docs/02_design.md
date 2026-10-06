# Template Design Specification — RET-C2-227

## Position in AgentCore Architecture

| Aspect | Value |
|--------|-------|
| Agent class | `RetailDocCatalogEnrichAgent` |
| L1 Base (framework base class) | `AgentBaseGraph` — direct framework inheritance |
| Inner graph base class | `BaseGraph` (`DomainWorkflowGraph`) |
| Category | Cat 2 — domain pipeline, nested graph |

- **Three-layer separation**
  - State: flat `TypedDict` composition (no Pydantic — msgpack incompatible)
  - Node: `FunctionNode` template method; subclasses override `execute()` only
  - Graph: composition via `register_nodes()`; the outer graph wraps the inner
    one through a `GraphNode` in the `main` slot

## Architecture Overview

### Node Configuration (outer backbone)

| Node | Responsibility | Trust Level | Inherits |
|------|---------------|-------------|----------|
| initialize | Session setup, schema version | — | `InitializeNode` (default) |
| pre_process (`PreProcessNode`) | Entry trust gate, caller-contract validation, injection screen, personal-data mask on the context channel | VERIFIED_EXTERNAL | `FunctionNode` |
| main (`DocCatalogEnrichGraphNode`) | Drive the inner document pipeline; bridge the validated contract across the boundary | ANONYMOUS | `GraphNode` |
| post_process (`PostProcessNode`) | Publish boundary: verify the approved shape, publish on success, clear and withhold otherwise | ANONYMOUS | `FunctionNode` |
| finalize | Response metadata, total time | — | `FinalizeNode` (default) |

### Inner Domain Nodes (`DomainWorkflowGraph`)

| Node | Responsibility | Trust Level |
|------|---------------|-------------|
| input_sanitize (`InputSanitizeNode`) | Settle the document reference from the contract and the request string; screen both | ANONYMOUS |
| doc_parse (`DocParseNode`) | Resolve the document text — caller, parse sidecar, or the illustrative sample — and record which | ANONYMOUS |
| attribute_extract_allergen_check (`AttributeExtractAndAllergenCheckNode`) | Product attributes and the mandatory 28-allergen check; report redaction honestly | ANONYMOUS |
| cert_validate (`CertValidateNode`) | Identify certification marks claimed in the document | ANONYMOUS |
| output_structure_validate (`OutputStructureAndOutputValidateNode`) | Assemble the catalog entry and enforce the published output invariant | ANONYMOUS |

### Data Flow

```
START -> initialize -> pre_process -> main (DocCatalogEnrichGraphNode) -> {route}
      -> post_process -> finalize -> END
                            | (retry, bounded by max_retry)
                            -> pre_process

Inside DocCatalogEnrichGraphNode (inner DomainWorkflowGraph):
  START -> input_sanitize -> doc_parse -> attribute_extract_allergen_check
        -> cert_validate -> output_structure_validate -> END
```

### Caller-data contract

Structured invocation parameters travel on `input_context`, a CLOSED set of
four fields. Undeclared keys are dropped at the adapter before the agent is
invoked — an undeclared key that is merely left unvalidated still reaches the
first node's result, where the framework's mandatory output scan reads it, so a
credential-shaped value under a key this template never declared would fail the
graph at node one with nothing naming the cause.

| Field | Type | Bound |
|-------|------|-------|
| `document_text` | string | `extraction.max_document_chars` (absolute ceiling 200,000) |
| `document_url` | string | 2,048 characters, URL alphabet only |
| `document_type` | enum | `PDF` \| `IMAGE` \| `EXCEL` \| `CSV` |
| `supplier_code` | string | inert identifier `[a-z0-9_]{1,32}` — it is rendered into the entry |

The document text travels here rather than in the request string because the
platform rewrites personal-data shapes out of the request string at every node
boundary, and its name heuristic reads title-case proper nouns as personal
names — which on a retail specification is what a product or brand name looks
like. This channel is not rewritten. It is also not covered by the platform's
personal-data mask, so this template applies that mask itself.

### State Definition

| Field | Type | Purpose | Set By |
|-------|------|---------|--------|
| caller_contract | Optional[str] | The validated contract, JSON-encoded | PreProcessNode / InputSanitizeNode |
| extraction_tuning | Optional[str] | Configured bounds seeded into the inner graph | DomainWorkflowGraph |
| document_url | Optional[str] | Reference to the supplier document | InputSanitizeNode |
| document_type | Optional[str] | Document format | InputSanitizeNode |
| supplier_code | Optional[str] | Inert supplier identifier | InputSanitizeNode |
| document_source | Optional[str] | `caller` \| `parser` \| `sample` | DocParseNode |
| raw_text | Optional[str] | The document text the extraction ran on | DocParseNode |
| extracted_attributes | Optional[str] | JSON: product name, price, ingredients, excerpt | AttributeExtractAndAllergenCheckNode |
| extraction_status | Optional[str] | `complete` \| `partial` \| `redacted` | AttributeExtractAndAllergenCheckNode |
| detected_allergens | Optional[str] | JSON list of mandatory allergen names present | AttributeExtractAndAllergenCheckNode |
| allergen_compliance | Optional[bool] | A usable allergen declaration is present | AttributeExtractAndAllergenCheckNode |
| certifications | Optional[str] | JSON list of recognised marks claimed | CertValidateNode |
| cert_compliance | Optional[bool] | At least one recognised mark is claimed | CertValidateNode |
| catalog_entry | Optional[str] | The structured JSON catalog entry | OutputStructureAndOutputValidateNode |

**State constraints (mandatory):**
- Flat `TypedDict` only (primitives and JSON-serializable values)
- No credentials or secrets in State (checkpoint storage would carry them)
- `InvocationContext` travels via `config["configurable"]`, never in State
- No Pydantic models, dataclasses or arbitrary objects (msgpack incompatible)

## Runtime Configuration

`config/agent.yaml` is the static manifest: identity and compile-time
requirements only. Runtime parameters live in `config/config.yaml`, which the
registry loads and passes to the graph constructor. The standalone entry point
does the same, so the same values are live in both deployments.

| Key | Read by | Effect of changing it |
|-----|---------|----------------------|
| `max_retry` | framework backbone routing | retry budget on the main slot |
| `timeout_s` | framework | request timeout |
| `extraction.max_document_chars` | contract validation, DocParseNode | a document above it is refused, not truncated |
| `extraction.max_field_chars` | AttributeExtractAndAllergenCheckNode, output boundary | length of every rendered attribute |
| `extraction.max_allergens` | AttributeExtractAndAllergenCheckNode | ceiling on reported allergens |
| `extraction.max_certifications` | CertValidateNode | ceiling on reported certifications |

Node `execute()` methods take no config argument, so the extraction block is
forwarded to the inner graph and seeded into inner state by its initial-state
hook. That is the only route a configured value can travel into a domain node.

## The Published Output Invariant

The catalog entry claims specific things about itself, and the output boundary
enforces every one of them rather than assuming the assembling code got them
right:

1. exactly the declared key set, no more and no fewer
2. every reported allergen is one of the 28 mandatory declarations
3. every reported certification is one of the recognised marks
4. every rendered string is within `extraction.max_field_chars`
5. `supplier_code` is an inert identifier
6. `document_source` and `extraction_status` are closed-set labels
7. no credential-shaped value appears anywhere in the entry

**The fleet's monetary rounding grid does not apply to this template.** It
renders no monetary aggregate: `price` is a label value copied from the
supplier's own document, and rounding it would report a figure the supplier
never quoted. The invariant above is what this template's schema actually
claims, so that is what the boundary enforces.

Clauses 2 and 3 matter most to a consumer. `allergens` is a compliance field,
and a value outside the mandatory list would be read as a declaration the
labelling rules do not recognise.

### What the certification check does and does not establish

`certifications_claimed` records that a recognised mark is CLAIMED in the
document. No registry lookup is performed, so the field is named for what was
checked. A deployment that needs verified certifications extends this node with
a registry call.

### Extraction status

`extraction_status` is `redacted` when the platform rewrote a value before the
extraction could read it. The field is reported empty and the status says why;
a redaction sentinel is never reported as an extracted product name.

## Framework Utilization

### Shared components used
- [x] `InvocationContext` (correlation id, session id, trust level, caller id)
- [x] `detect_pii` / `mask_pii` — applied by this template to the context channel
- [x] `detect_credentials_in_value` — the floor of the output boundary's detector,
      and the adapter's context screen
- [x] Input gate extension point `_extra_security_gate_input()` — the framework's
      default scan runs automatically on `FunctionNode` subclasses
- [x] Output gate extension point `_extra_security_gate_output()`
- [x] `emit_trace_event()` — emitted in every domain node `execute()`

> **Input/output gate behaviour by node type:**
> - `FunctionNode` subclass → the framework's `@final` gates always run;
>   extend via `_extra_security_gate_input()` / `_extra_security_gate_output()` only
> - `GraphNode` / `RemoteAgentNode` → deliberate no-op at the subgraph boundary
> - A direct `BaseNode` subclass must implement both gates itself

### Composition pattern

- **Pattern**: `GraphNode` (subgraph)
- **Composition target**: `DomainWorkflowGraph` (inner `BaseGraph`)
- **Error propagation strategy**: propagate
- **Contract bridge**: `src/graph/context_bridge.py`. The framework invokes a
  nested graph with the request STRING only, so the validated structured
  contract crosses on a `ContextVar` set in `extract_input()` and read by the
  inner graph's `_extra_initial_state()`.

## Security Gate Placement

| Layer | Gate | Location |
|-------|------|----------|
| S-1 | Entry trust gate (VERIFIED_EXTERNAL) | `PreProcessNode` |
| S-1 | Caller authentication for the standalone entry point | `src/api/server.py` (`INVOKE_AUTH_TOKEN` bearer) |
| S-2 | Injection screen — control tokens, markup markers, directives; raw and markup-stripped, keys included | `src/services/screens.py`, applied in `PreProcessNode` and `InputSanitizeNode` |
| S-2 | Caller-contract validation — closed field set, bounds, inert alphabets | `src/services/contract.py`, applied at the adapter and in `PreProcessNode` |
| S-2 | Personal-data mask on the context channel | `PreProcessNode` |
| S-2 | Credential screen on the context channel, before `invoke()` | `src/api/server.py` |
| S-3 | Output invariant, including the credential union | `OutputStructureAndOutputValidateNode` |
| S-3 | Publish boundary: shape verification, clearing, withheld notice | `PostProcessNode` |
| S-3 | Envelope override: a non-success status never falls back to the raw result | `RetailDocCatalogEnrichAgent.get_output()` |
| S-4 | Audit events | every domain node `execute()` |
| S-5 | Secrets | `secrets_factory()` in `src/api/server.py`; no hardcoded credentials |

### Why containment takes three places

The framework's envelope resolves the caller-visible output as the formatted
output OR the raw result, with no status check. Two paths cannot be closed by
the boundary node alone: an error routes straight to finalize, so the publish
node never runs; and when the framework's own scan raises on a node's return
value, that node's whole delta — its clearing included — is discarded.

The success path is checked in exactly ONE place. A second scan there would
contain a leak by itself and make the first unfalsifiable.

### Why the credential detector is a union

The framework's patterns describe credential FORMATS and match none of the
`password=` / `api_key=` assignment shapes a document plausibly carries. This
template's patterns are the mirror image. Either alone is a narrower gate than
both, and a narrower output gate is a bypass rather than a simplification.

## Import Isolation Confirmation
- [x] No platform SDK import (Level 0)
- [x] Import targets: `framework/` and `shared/` only

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | AgentBaseGraph | Fixed multi-step pipeline; no autonomous reasoning loop |
| Composition pattern | Flat Cat 1 slots | Cat 2 GraphNode (nested) | Cat 2 GraphNode | A five-step domain workflow belongs in an inner graph |
| Inner graph parent | BaseGraph | AgentBaseGraph | BaseGraph | Fully custom topology; no backbone slots needed inside |
| Document source | Sidecar only | Caller text, then sidecar, then sample | Three sources, recorded | A sidecar-only path returns the same answer for every document wherever the sidecar is unreachable, and says nothing about it |
| Oversized document | Truncate | Refuse | Refuse | An allergen declared past a truncation point would be absent from a document the entry then reports as compliant |
| Allergen list | Configurable | The 28 mandatory declarations | The 28 | The list is regulatory, not a template setting |
| Certification check | Registry lookup | Pattern match against a closed list | Pattern match, named as a claim | No registry call is made, so the field says "claimed" |
| Output invariant | Monetary rounding grid | The template's own schema claims | The schema claims | No monetary aggregate is rendered; rounding a supplier's quoted price would misreport it |
