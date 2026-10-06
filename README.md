# Document Parse & Catalog Enrichment Agent

AI agent for parsing supplier documents and enriching retail catalog entries, built with Agentic Star.

> **Category**: Cat 2 (a domain-specific pipeline for one job-to-be-done)
> **Industry**: Retail
> **Template ID**: RET-C2-227

## Overview

Retailers onboard products from supplier documents — specification sheets,
label extracts, price lists — and a catalog entry cannot be published until
someone has read the document, typed the attributes into the catalog, and
confirmed that the mandatory allergen declaration and the certification marks
are there. That reading is slow, it is repeated for every product, and a missed
allergen is a labelling failure rather than a data-quality one.

This agent takes one supplier document and returns one structured **catalog
entry**: product name, price and ingredients; the mandatory allergen
declarations the document carries; the certification marks it claims; and
compliance flags a downstream catalog system can act on.

It is careful about what it claims. The entry records where its text came from,
so a caller can tell an extraction from a placeholder. It reports allergens and
certifications only from closed regulatory lists, so an unrecognised string in a
document never becomes a declaration. It names the certification field for what
was checked — a mark *claimed* in the document, not a verified certification.
And where the platform redacted a value before the extraction could read it, the
entry says so rather than reporting the redaction as an extracted value.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >= 3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent fails at graph
compile / start-up preflight rather than starting in a partially working state. This is
intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Project Structure

```
src/          agent implementation (nodes, graphs, services, schemas)
tests/        unit, integration and boundary tests
config/       agent manifest and runtime parameters
deploy/       local deployment recipe and the smoke payload
docs/         design and operational documentation
```

`docs/02_design.md` describes the architecture, the caller contract and the output
invariant; `docs/03_test_spec.md` lists what the suite proves.

## Sending a Document

The document text belongs in `input_context`, not in the request string:

```json
{
  "input": "Enrich the catalog entry for this supplier specification.",
  "input_context": {
    "document_text": "製品名: ...\n価格: ...\n原材料: ...\n認証: ...",
    "document_type": "PDF",
    "supplier_code": "supplier_00417"
  }
}
```

The platform rewrites personal-data shapes out of the request string at every
node boundary, and its name heuristic reads title-case proper nouns as personal
names — which on a retail specification is what a product or brand name looks
like. Text sent on `input_context` is not rewritten.

`docs/02_design.md` carries the full contract, the response schema and the
output invariant the boundary enforces.

## Customising

1. Adjust `config/config.yaml` for your own bounds — the document ceiling and
   the rendered-field cap both change what the released entry contains.
2. Extend the recognised certification marks in `src/nodes/cert_validate_node.py`
   if your suppliers carry others, and the document field labels in
   `src/nodes/attribute_extract_and_allergen_check_node.py` if your documents
   use a different layout.
3. Point `src/services/service.py` at your own document parser.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
