"""Knowledge base MCP endpoint stub served over real Streamable HTTP.

It returns results shaped like Azure AI Search's ``knowledge_base_retrieve``:
a JSON array of passages plus one reference per passage. Retrieval itself is
not simulated; the real knowledge base is checked with scripts/query-knowledge.sh.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import TextContent

KB_NAME = "conocimiento-test"

CARTA_PASSAGE = (
    "### morcilla-de-burgos-a-la-brasa · Morcilla de Burgos a la brasa\n"
    "- Fuente: carta de la casa, versión 1 (documento: carta)\n"
    "- Partida: brasa\n"
    "- Precio: 8,50 € la ración de cuatro rodajas"
)
CARTA_REFERENCE = {
    "kind": "reference",
    "ref_id": 0,
    "uri": "https://stdemo.blob.core.windows.net/carta/carta.md",
    "mimeType": "application/json",
    "sourceData": {
        "uid": "abc",
        "blob_url": "https://stdemo.blob.core.windows.net/carta/carta.md",
        "snippet": CARTA_PASSAGE,
    },
}


@dataclass
class StubKnowledge:
    passages: list[dict[str, Any]] = field(
        default_factory=lambda: [{"ref_id": 0, "content": CARTA_PASSAGE}]
    )
    references: list[dict[str, Any]] = field(default_factory=lambda: [CARTA_REFERENCE])
    queries: list[list[str]] = field(default_factory=list)
    authorizations: list[str | None] = field(default_factory=list)
    fail: bool = False
    delay_seconds: float = 0.0


def build_server(stub: StubKnowledge, port: int) -> FastMCP:
    server = FastMCP(
        "Stub knowledge base", host="127.0.0.1", port=port,
        streamable_http_path=f"/knowledgebases/{KB_NAME}/mcp",
    )

    @server.tool()
    def knowledge_base_retrieve(query_variants: list[str], ctx: Context) -> list[TextContent]:
        request = ctx.request_context.request
        stub.authorizations.append(request.headers.get("authorization") if request else None)
        stub.queries.append(query_variants)
        if stub.delay_seconds:
            time.sleep(stub.delay_seconds)
        if stub.fail:
            raise RuntimeError("the knowledge base timed out")
        return [
            TextContent(type="text", text=json.dumps(stub.passages, ensure_ascii=False)),
            *(
                TextContent(type="text", text=json.dumps(reference, ensure_ascii=False))
                for reference in stub.references
            ),
        ]

    return server
