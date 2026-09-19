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

# Arguments no tool may ever take. The surface is bounded by what does not
# exist, not by instructions asking the model to behave.
FORBIDDEN_ARGUMENT_NAMES = {
    "path",
    "file_path",
    "filename",
    "directory",
    "output_path",
    "url",
    "uri",
    "command",
    "shell",
    "cmd",
    "script",
    "api_key",
    "token",
    "secret",
    "password",
    "credential",
    "cookie",
    "cookies",
    "env",
}


def _schema_of(tool) -> dict:
    schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)
    return schema or {}


class McpContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_frozen_tool_surface_is_present(self) -> None:
        async with Client(mcp) as client:
            result = await client.list_tools()
            names = {tool.name for tool in result.tools}
        self.assertEqual(names, EXPECTED_TOOLS)

    async def test_no_tool_accepts_a_path_url_command_or_secret(self) -> None:
        """The surface is bounded by what does not exist.

        A model that misreads a hostile transcript still cannot ask this server
        to open a file, fetch a URL or run a command, because no argument that
        would carry one is defined anywhere.
        """
        async with Client(mcp) as client:
            tools = (await client.list_tools()).tools

        offences: list[str] = []
        for tool in tools:
            properties = _schema_of(tool).get("properties") or {}
            for argument in properties:
                if argument.lower() in FORBIDDEN_ARGUMENT_NAMES:
                    offences.append(f"{tool.name}.{argument}")

        self.assertEqual(offences, [], f"forbidden arguments exposed: {offences}")

    async def test_every_tool_is_documented(self) -> None:
        async with Client(mcp) as client:
            tools = (await client.list_tools()).tools

        undocumented = [t.name for t in tools if not (t.description or "").strip()]
        self.assertEqual(undocumented, [])


if __name__ == "__main__":
    unittest.main()
