"""Re-export of the canonical multi-client generator model (single source of truth).

The actual implementation lives in generators/canonical_model.py (shared by the
Bash/PowerShell generators via `python -m generators.canonical_model`). This
module exists so the equivalence tests can import the reference renderer and
compare all generators against it.

See docs/MULTI_CLIENT_DEPLOYMENT_GUIDE.md for the canonical input model.
"""

from generators.canonical_model import (  # noqa: F401
    ACCESS_METHODS,
    MCP_IMAGE_BASE,
    TUNNEL_IMAGE_BASE,
    normalize,
    render,
    validate,
)

__all__ = ["ACCESS_METHODS", "MCP_IMAGE_BASE", "TUNNEL_IMAGE_BASE",
           "normalize", "render", "validate"]