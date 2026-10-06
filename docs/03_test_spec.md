# Test Specification — RET-C2-227 Retail Document Parse & Catalog Enrichment

## 1. Overview

RET-C2-227 turns a supplier product document into a structured catalog entry:
product attributes, the mandatory 28-allergen check, and the certification
marks the document claims.

**Test scope**
- The outer backbone nodes (`PreProcessNode`, `PostProcessNode`)
- The inner pipeline nodes (`InputSanitizeNode`, `DocParseNode`,
  `AttributeExtractAndAllergenCheckNode`, `CertValidateNode`,
  `OutputStructureAndOutputValidateNode`)
- The caller-data contract and the content screens
- End-to-end behaviour through the real ASGI entry point at the declared trust
  level, including the deploy smoke payload
- Boundary tests: backbone order, trust gate, output boundary, state safety,
  import isolation

**Two rules the suite is built on.**

*Nodes are driven through `execute()` directly.* The platform's own input gate
refuses some payloads before a node runs, so an end-to-end test cannot tell
whether the template refused or the platform did — and where that gate is
absent, only the template's refusal remains. Calling `execute()` measures the
template.

*The entry point is exercised for real.* A request can be well-formed HTTP,
answered 200, and carry valid JSON while the agent inside answers "error" to
every call. Only driving the real app at the declared trust level sees that.

**Test files**

| File | Covers |
|------|--------|
| `tests/unit/test_agent.py` | every node, through `execute()` |
| `tests/unit/test_screens.py` | injection and credential screens, both directions |
| `tests/unit/test_contract.py` | the caller-data contract |
| `tests/unit/test_framework_compliance_tc06_tc07.py` | the default gates cannot be replaced |
| `tests/integration/test_invoke_contract.py` | end to end through the real ASGI app |
| `tests/integration/test_manifest_identity_alignment.py` | provisioned identity vs the manifest |
| `tests/integration/test_stg_payload_contract.py` | the committed deploy payload |
| `tests/proof_of_boundary/test_pb_invoke_order.py` | backbone execution order |
| `tests/proof_of_boundary/test_s1_trust_gate.py` | trust-level declarations and enforcement |
| `tests/proof_of_boundary/test_s3_output_gate.py` | the output boundary and the caller-visible envelope |
| `tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py` | interrupt propagation (skipped: not enabled here) |
| `tests/proof_of_boundary/test_import_isolation.py` | no platform SDK import |
| `tests/proof_of_boundary/test_state_safety.py` | State carries only checkpoint-safe types |

---

## 2. Node Test Cases

### 2.1 PreProcessNode (TC-PRE)

| ID | Description | Expected |
|----|-------------|----------|
| TC-PRE-01 | Entry contract is VERIFIED_EXTERNAL | class declares it |
| TC-PRE-02 | Valid request produces a validated contract | SUCCESS, `caller_contract` set |
| TC-PRE-03 | Empty, whitespace-only or non-string request | ERROR |
| TC-PRE-04 | This node refuses injection itself, no framework wrapper in front | ERROR, no `validated_input` |
| TC-PRE-05 | Injection in the structured parameters | ERROR |
| TC-PRE-06 | A rejected field is named; its value is not echoed | field name present, value absent |
| TC-PRE-07 | Personal data on the context channel is masked | sentinel present, original absent |

### 2.2 InputSanitizeNode (TC-ISN)

| ID | Description | Expected |
|----|-------------|----------|
| TC-ISN-01 | Contract document carried through | SUCCESS, fields settled |
| TC-ISN-02 | Legacy JSON request shape still works | SUCCESS, url and type set |
| TC-ISN-03 | A bare reference is accepted as a document URL | `document_url` set |
| TC-ISN-04 | Prose is not mistaken for a document reference | SUCCESS, `document_url` empty |
| TC-ISN-05 | A request naming no document | ERROR |
| TC-ISN-06 | Injection is refused on this path too | ERROR |
| TC-ISN-07 | Trust level declared | ANONYMOUS |

### 2.3 DocParseNode (TC-DPN)

| ID | Description | Expected |
|----|-------------|----------|
| TC-DPN-01 | Caller text is used and labelled | `document_source == "caller"` |
| TC-DPN-02 | Without caller text, the illustrative document is labelled as such | `document_source == "sample"` |
| TC-DPN-03 | A document over the ceiling is refused, not truncated | ERROR, no `raw_text` |
| TC-DPN-04 | No document at all | ERROR |

### 2.4 AttributeExtractAndAllergenCheckNode (TC-AEAC)

| ID | Description | Expected |
|----|-------------|----------|
| TC-AEAC-01 | Fields extracted from a multi-line document | name, price, ingredients correct |
| TC-AEAC-02 | A field ends at the next label on a single-line document | name does not swallow the rest |
| TC-AEAC-03 | Detected allergens are a subset of the mandatory list | subset holds |
| TC-AEAC-04 | A document with no declaration is not reported compliant | `allergen_compliance` false |
| TC-AEAC-05 | A redacted value is never reported as an extraction | field empty, status `redacted` |
| TC-AEAC-06 | No product name means `partial`, not `complete` | status `partial` |
| TC-AEAC-07 | Every rendered field is capped | all within the configured cap |
| TC-AEAC-08 | Missing text | ERROR |

### 2.5 CertValidateNode (TC-CVN)

| ID | Description | Expected |
|----|-------------|----------|
| TC-CVN-01 | Recognised marks are reported | subset of the recognised list |
| TC-CVN-02 | An unrecognised string never becomes a certification | empty list, claim absent |
| TC-CVN-03 | The label is bounded to its own line | no spill from following blocks |
| TC-CVN-04 | Missing text | ERROR |

### 2.6 The output boundary (TC-OUT)

| ID | Description | Expected |
|----|-------------|----------|
| TC-OUT-01 | The approved entry passes | no exception |
| TC-OUT-02 | The key set is exact — extra or missing key | refused |
| TC-OUT-03 | An allergen outside the mandatory declarations | refused |
| TC-OUT-04 | A certification outside the recognised marks | refused |
| TC-OUT-05 | An oversized rendered field | refused |
| TC-OUT-06 | A non-inert supplier code | refused |
| TC-OUT-07 | A closed-set label out of set | refused |
| TC-OUT-08 | A credential-shaped value, framework formats and local assignment shapes | refused |
| TC-OUT-09 | A credential nested one level deep | refused |
| TC-OUT-10 | The violation message never carries the value | value absent |
| TC-OUT-11 | A violation clears the output-bearing field | `catalog_entry == ""`, ERROR |

### 2.7 PostProcessNode (TC-POST)

| ID | Description | Expected |
|----|-------------|----------|
| TC-POST-01 | An approved entry is published | `formatted_output == result` |
| TC-POST-02 | Nothing to publish yields a TRUTHY notice and cleared fields | notice set, `result` None, ERROR |
| TC-POST-03 | A non-success upstream status is never published | notice set, `result` None |
| TC-POST-04 | A value that is not the approved shape is withheld | notice set |
| TC-POST-05 | Trust level declared | ANONYMOUS |

> TC-POST-02 previously asserted `formatted_output == ""`. An empty string is
> falsy, and the envelope resolves the output as the formatted output OR the
> raw result — so that assertion asserted the exact value that re-opens the
> fallback this node exists to close. It now requires the truthy notice.

### 2.8 The content screens (TC-SCR)

| ID | Description | Expected |
|----|-------------|----------|
| TC-SCR-01 | Chat-template control tokens caught as a class | `control_token` |
| TC-SCR-02 | Directive phrases caught | `directive` |
| TC-SCR-03 | Scripting markers caught | `markup_marker` |
| TC-SCR-04 | Real supplier wording is NOT refused | no finding |
| TC-SCR-05 | A directive split by inert tags is caught after stripping | `directive` |
| TC-SCR-06 | A control token is caught before stripping could delete it | `control_token` |
| TC-SCR-07 | Keys are screened, not only values | `control_token` |
| TC-SCR-08 | Nested structures walked depth first | finding returned |
| TC-SCR-09 | A `\u`-escaped payload is seen after parsing | finding returned |
| TC-SCR-10 | Framework credential formats caught | findings |
| TC-SCR-11 | Local assignment shapes caught | findings |
| TC-SCR-12 | The local half is load-bearing (framework misses those shapes) | framework returns none |
| TC-SCR-13 | Ordinary catalog content is not a finding | no findings |
| TC-SCR-14 | Findings never carry the matched text | value absent |

### 2.9 The caller contract (TC-CON)

| ID | Description | Expected |
|----|-------------|----------|
| TC-CON-01 | Undeclared keys are dropped, not ignored | absent from the selection |
| TC-CON-02 | A non-mapping is an empty contract | `{}` |
| TC-CON-03 | The declared field set is the published one | exact match |
| TC-CON-04 | A valid contract round-trips | unchanged |
| TC-CON-05 | `document_type` normalised and closed | enum enforced |
| TC-CON-06 | Out-of-alphabet values refused by field | field named |
| TC-CON-07 | Non-string values refused, bool included | refused |
| TC-CON-08 | `document_text` bounded by the configured ceiling | refused above it |
| TC-CON-09 | A misconfigured ceiling cannot widen the contract | absolute ceiling holds |
| TC-CON-10 | A rejected value is never echoed | value absent from the message |

---

## 3. Boundary Tests

### 3.1 Backbone invoke order (TC-PB6)

| ID | Description | Expected |
|----|-------------|----------|
| TC-PB6-01 | A VERIFIED_EXTERNAL caller drives the full backbone | SUCCESS, output non-None, node order exact |
| TC-PB6-02 | The main slot is a `GraphNode` subclass | subclass check |
| TC-PB6-03 | An ANONYMOUS caller is refused at the entry gate | status != SUCCESS |

### 3.2 Trust gate (TC-S1)

| ID | Description | Expected |
|----|-------------|----------|
| TC-S1-01 | `PreProcessNode` declares VERIFIED_EXTERNAL | declared |
| TC-S1-02..08 | Every other node declares its level explicitly | ANONYMOUS each |
| TC-S1-09 | An ANONYMOUS caller is denied before `execute()` | ERROR |
| TC-S1-10 | A VERIFIED_EXTERNAL caller is admitted | SUCCESS |

### 3.3 The output boundary and the envelope (TC-S3)

| ID | Description | Expected |
|----|-------------|----------|
| TC-S3-01 | A clean entry is released | SUCCESS |
| TC-S3-02 | A credential-bearing entry is withheld and cleared | ERROR, entry empty, value absent |
| TC-S3-03 | The invariant covers more than credentials | each clause refused |
| TC-S3-04 | A clean request publishes the entry (real compiled graph) | SUCCESS |
| TC-S3-05 | The error envelope releases nothing — no value, no traceback, no paths, no entry text | all absent |
| TC-S3-06 | The envelope never falls back to the raw result on a non-success status | output None |

---

## 4. End-to-End Test Cases (TC-E2E)

Driven through the real ASGI app with a bearer credential.

| ID | Description | Expected |
|----|-------------|----------|
| TC-E2E-01 | Health endpoint | ok |
| TC-E2E-02 | An authenticated request returns a real entry | SUCCESS, extracted values present |
| TC-E2E-03 | Absent, malformed and wrong credentials are indistinguishable | 401, generic message |
| TC-E2E-04 | Two different documents produce two different entries | name, price, allergens and certifications all differ |
| TC-E2E-05 | Without caller text the entry says where it came from | `document_source == "sample"` |
| TC-E2E-06 | A document with no declaration is not reported compliant | flags false |
| TC-E2E-07 | The contract reaches the inner pipeline | caller text present in the entry |
| TC-E2E-08 | An invalid field is refused by name, value not echoed | 400 |
| TC-E2E-09 | Undeclared keys are dropped rather than reaching the graph | 200 SUCCESS |
| TC-E2E-10 | An oversized payload is refused before the graph | 413 |
| TC-E2E-11 | A credential in the document is refused readably | 400 naming the field |
| TC-E2E-12 | Ordinary document text on the same field still passes | SUCCESS |
| TC-E2E-13 | Assignment shapes the framework misses are caught at the output boundary | status error, value absent |
| TC-E2E-14 | The error envelope carries no released text, traceback or paths | all absent |
| TC-E2E-15 | Attacks do not reach a successful answer | status != success |
| TC-E2E-16 | Real supplier wording is still processed | SUCCESS |
| TC-E2E-17 | A redacted product name is reported as redacted | field empty, status `redacted` |
| TC-E2E-18 | A declared configuration bound changes what the agent returns | rendered length follows the config |

### 4.1 The deploy smoke payload (TC-STG)

| ID | Description | Expected |
|----|-------------|----------|
| TC-STG-01 | The payload file exists and is valid JSON | parses |
| TC-STG-02 | The payload IS the repository fixture, not a value invented for the deploy | byte-equal |
| TC-STG-03 | Posting the committed payload succeeds — the agent, not just the HTTP call | SUCCESS, real extraction |
| TC-STG-04 | A payload the contract rejects really does fail | 400 |
| TC-STG-05 | The payload names no credential and no internal host | clean |

> TC-STG-03 and TC-STG-04 are a pair on purpose. The deploy job tolerates
> failure, so a refused invoke leaves a green pipeline: the HTTP checks all
> pass and only the evidence artifact records the agent's own error. Probing
> the good case alone cannot distinguish a real check from a broken one.

---

## 5. Security Test Coverage

| Layer | Coverage |
|-------|---------|
| S-1 | Entry trust gate (TC-S1-09/10, TC-PB6-03); adapter authentication (TC-E2E-03) |
| S-2 | Injection screen both directions (TC-SCR-01..09, TC-PRE-04/05, TC-ISN-06, TC-E2E-15/16); contract bounds (TC-CON-\*, TC-E2E-08/10); personal-data mask on the context channel (TC-PRE-07); credential screen on the context channel (TC-E2E-11/12) |
| S-3 | Output invariant (TC-OUT-\*, TC-S3-01..03); containment (TC-OUT-11, TC-POST-02/03/04, TC-S3-05/06, TC-E2E-13/14) |
| S-4 | Audit events emitted in every domain node; silenced by patching the imported name per node module, so the tests need no audit backend |
| S-5 | Provisioned identity matches the manifest (TC-IDENT below); no hardcoded credentials anywhere in source |

### 5.1 Provisioned identity (TC-IDENT)

| ID | Description | Expected |
|----|-------------|----------|
| TC-IDENT-01 | The manifest declares the identity fields | present |
| TC-IDENT-02 | The provisioned namespace matches the manifest | equal |
| TC-IDENT-03 | The provisioned agent name matches the manifest | equal |
| TC-IDENT-04 | The manifest namespace is lower(industry) | equal |
| TC-IDENT-05 | The manifest class resolves to the agent the entry point serves | isinstance |

> Both sides are READ from their files. A test that spelled the expected
> namespace out twice would keep passing through exactly the drift it exists to
> catch — one agent's secrets split across two stores, with no error at boot
> because a missing tier file is ignored by design.

---

## 6. Test Environment Requirements

- Python 3.11+
- The `agenticstar-agentcore` wheel at the version CI installs
- pytest 9.0.3, pytest-cov 5.0.0, pytest-asyncio 1.4.0
- No parse sidecar required: the document text comes from the caller in every
  test, and the sidecar path degrades to the illustrative document
- No stubbing of `shared.*` in `sys.modules` — the wheel provides the real package
