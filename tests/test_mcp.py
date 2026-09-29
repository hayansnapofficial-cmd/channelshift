"""Official SDK client over actual stdio, with isolated local project state."""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mcp import Client, StdioServerParameters


class MCPTests(unittest.TestCase):
    def test_all_eight_tools_over_stdio(self):
        async def exercise(folder):
            parameters = StdioServerParameters(command=sys.executable, args=["-m", "channelshift.mcp_server"],
                                                env={**os.environ, "CHANNELSHIFT_HOME": folder})
            async with Client(parameters, mode="legacy", read_timeout_seconds=20) as client:
                tools = (await client.list_tools()).tools
                expected = {"list_templates", "create_schema_from_template", "validate_schema", "export_sql", "export_java", "save_project", "list_projects", "get_project"}
                self.assertEqual(expected, {item.name for item in tools})
                for tool in tools:
                    self.assertFalse(tool.annotations.open_world_hint)
                    self.assertEqual(tool.name != "save_project", tool.annotations.read_only_hint)
                async def use(name, args=None):
                    result = await client.call_tool(name, args or {})
                    self.assertFalse(result.is_error, result.content)
                    return result.structured_content
                templates = (await use("list_templates"))["items"]
                self.assertEqual(4, len(templates))
                schema = (await use("create_schema_from_template", {"template_id": "booking", "project": "MCP booking", "database": "sqlite"}))["schema"]
                self.assertTrue((await use("validate_schema", {"schema": schema}))["valid"])
                self.assertIn("CREATE TABLE", (await use("export_sql", {"schema": schema}))["files"][0]["content"])
                self.assertTrue(any(item["path"].endswith("Repository.java") for item in (await use("export_java", {"schema": schema}))["files"]))
                saved = await use("save_project", {"schema": schema, "topic": "booking"})
                self.assertTrue(saved["stored"])
                self.assertFalse((await use("save_project", {"schema": schema, "topic": "booking"}))["stored"])
                self.assertEqual(1, len((await use("list_projects"))["items"]))
                self.assertEqual(schema, (await use("get_project", {"project_id": saved["id"]}))["schema"])
                for name, arguments in (("validate_schema", {"schema": "TEST_SECRET_MARKER"}), ("list_templates", {"path": "TEST_SECRET_MARKER"}), ("get_project", {"project_id": "../../TEST_SECRET_MARKER"})):
                    result = await client.call_tool(name, arguments)
                    self.assertTrue(result.is_error)
                    self.assertNotIn("TEST_SECRET_MARKER", str(result.model_dump()))
                self.assertEqual(1, len(list(Path(folder).rglob("*.json"))))
        with tempfile.TemporaryDirectory(prefix="channelshift-mcp-test-") as folder:
            asyncio.run(exercise(folder))

    def test_malformed_raw_params_are_sanitized(self):
        from channelshift.mcp_server import check_call
        async def exercise():
            async def unused(_):
                self.fail("Malformed call reached SDK validation")
            for params in (["private marker"], {"name": []}, {"name": {}}, {"name": "save_project", "arguments": []}):
                result = await check_call(SimpleNamespace(method="tools/call", params=params), unused)
                self.assertTrue(result.is_error)
                self.assertNotIn("private marker", str(result.model_dump()))
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
