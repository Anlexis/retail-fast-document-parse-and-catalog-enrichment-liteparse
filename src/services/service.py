"""AgentCore Platform v1.0"""

# Service layer: where the document text comes from.
#
# Three sources, in priority order, and the catalog entry always records which
# one was used:
#
#   "caller"  the caller supplied the document text with the request. This is
#             the path that produces a real extraction without a parser
#             deployment, and it is the path the tests and the deploy payload
#             exercise.
#   "parser"  the document-parse sidecar returned text for the referenced
#             document. Used when a sidecar is reachable.
#   "sample"  neither was available. A fixed illustrative document is returned
#             so the pipeline still has something well-formed to work on.
#
# Recording the source matters more than it looks. Before it existed, an
# unreachable sidecar silently fell back to the fixed document, so every
# request — whatever it referenced — produced byte-identical product
# attributes, allergens and certifications, and nothing in the response said
# so. A caller had no way to tell an extraction from a placeholder. The
# priority order is unchanged; what changed is that the answer now says which
# branch it came from, and callers who need a real extraction can supply the
# text themselves.

from __future__ import annotations

import json
import urllib.request
from typing import Any, Tuple

# The sidecar is expected on the local pod; it is never reached across a
# network boundary, so a short timeout is the right failure mode.
_PARSER_ENDPOINT = "http://localhost:8765/parse"
_PARSER_TIMEOUT_S = 5

SOURCE_CALLER = "caller"
SOURCE_PARSER = "parser"
SOURCE_SAMPLE = "sample"

# A well-formed illustrative supplier document, used only when there is neither
# caller text nor a reachable parser. It is deliberately laid out over separate
# lines, the way a real label extract is, so the field patterns see the same
# shape they would in production.
_SAMPLE_DOCUMENT = "\n".join(
    (
        "製品名: サンプル製品",
        "価格: 1000円",
        "原材料: 小麦, 乳, 大豆, えび",
        "アレルゲン: 小麦・乳・大豆・えび",
        "認証: JAS, ISO22000",
    )
)


class DocumentSource:
    """Resolve the document text for one request."""

    def resolve(
        self,
        caller_text: str = "",
        document_url: str = "",
        document_type: str = "PDF",
    ) -> Tuple[str, str]:
        """Return ``(text, source)`` for this request.

        Never raises: an unreachable or malformed parser response degrades to
        the illustrative document rather than failing the request, and the
        returned source says which branch was taken.
        """
        if caller_text and caller_text.strip():
            return caller_text, SOURCE_CALLER

        if document_url:
            parsed = self._call_parser(document_url, document_type)
            if parsed and parsed.strip():
                return parsed, SOURCE_PARSER

        return _SAMPLE_DOCUMENT, SOURCE_SAMPLE

    def _call_parser(self, document_url: str, document_type: str) -> str:
        """Ask the parse sidecar for the document text; "" when unavailable."""
        try:
            payload = json.dumps({"url": document_url, "doc_type": document_type}).encode()
            request = urllib.request.Request(
                _PARSER_ENDPOINT,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=_PARSER_TIMEOUT_S) as response:
                body: Any = json.loads(response.read())
            return str(body.get("text", "")) if isinstance(body, dict) else ""
        except Exception:  # noqa: BLE001 — any transport or decode failure degrades
            return ""
