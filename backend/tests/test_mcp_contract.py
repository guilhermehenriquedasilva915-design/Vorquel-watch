import unittest

from mcp import Client

from vorquel_watch.mcp_server import mcp


EXPECTED_TOOLS = {
    "get_capabilities",
    "list_sources",
    "get_source",
    "start_analysis",
    "get_job",
    "cancel_job",
    "get_transcript",
    "search_transcript",
    "get_segment",
    "list_speakers",
    "get_speaker_turns",
    "create_export",
    "list_artifacts",
    "get_artifact",
}


class McpContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_frozen_tool_surface_is_present(self) -> None:
        async with Client(mcp) as client:
            result = await client.list_tools()
            names = {tool.name for tool in result.tools}
        self.assertEqual(names, EXPECTED_TOOLS)


if __name__ == "__main__":
    unittest.main()
