"""The JSON-RPC envelope every MCP request arrives in.

MCP is JSON-RPC 2.0, and the split matters: the envelope is fixed by the protocol, while
``params`` is whatever the addressed method takes. For ``tools/call`` that is tool-specific by
design -- each tool publishes an ``inputSchema`` and the broker validates arguments against the
schema the tool declared. So the envelope is typed here and the payload is validated there,
which is the same division the protocol makes.

Typing the envelope is also what keeps a raw ``request.json()`` value from reaching a service
call unvalidated. Parsing it by hand worked, but it left the boundary looking untyped to anyone
reading the router, and the data-flow gate could not tell a hand-rolled check from no check.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class JsonRpcEnvelope(BaseModel):
    """One JSON-RPC 2.0 request or notification, per the MCP wire format."""

    # Unknown members are ignored rather than rejected. JSON-RPC 2.0 defines exactly these four,
    # but a client that adds a member is not attacking anything, and refusing it would break an
    # MCP client over a field nobody reads.
    model_config = ConfigDict(extra="ignore")

    jsonrpc: Literal["2.0"]
    method: str
    # Absent on a notification, which is how JSON-RPC distinguishes "no reply expected".
    id: str | int | None = None
    params: dict[str, object] = Field(default_factory=dict[str, object])

    @property
    def is_notification(self) -> bool:
        return self.id is None
