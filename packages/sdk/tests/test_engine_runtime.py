from __future__ import annotations

import unittest

from data_intelligence_sdk.runtime.engine_runtime import EngineRuntimeContext
from data_intelligence_sdk.runtime.mcp_client import MCPToolDefinition


class EngineRuntimeContextTests(unittest.TestCase):
    def test_has_mcp_tools_requires_client_and_tools(self) -> None:
        client = object()
        tool = MCPToolDefinition(name="lookup")

        self.assertFalse(EngineRuntimeContext().has_mcp_tools)
        self.assertFalse(EngineRuntimeContext(mcp_client=client).has_mcp_tools)
        self.assertTrue(
            EngineRuntimeContext(mcp_client=client, mcp_tools=(tool,)).has_mcp_tools
        )


if __name__ == "__main__":
    unittest.main()
