from __future__ import annotations

from .wb_mcp_runtime_patch import apply_wb_mcp_hotfixes


def main() -> None:
    # Patch the exact WBClient class used by the local MCP subprocess before
    # wb_mcp.server imports its dispatch table and creates any clients.
    apply_wb_mcp_hotfixes()
    from wb_mcp.server import main as wb_mcp_main
    wb_mcp_main()


if __name__ == "__main__":
    main()
