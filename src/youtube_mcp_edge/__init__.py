"""Public Remote MCP edge — Stage 0/1.

A separate, self-hostable edge component that proxies public Streamable HTTP MCP
traffic to the private MCP core after static Bearer authentication.

Stage 0 scope: separate service, /healthz, /mcp proxy to the core, request-size
protection, safe logging, private upstream connection.
Stage 1 scope: canonical static `Authorization: Bearer <token>` authentication.

The edge intentionally contains NO MCP tool/business logic and does NOT duplicate
the MCP core. It is a transparent Streamable HTTP proxy after authentication.
OAuth / TLS termination / certificate automation are explicitly out of scope for
Stage 0/1 (see docs/MULTI_CLIENT_MCP_PHASE1_ARCHITECTURE.md).
"""

__version__ = "0.1.0"