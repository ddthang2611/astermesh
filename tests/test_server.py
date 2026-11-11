import asyncio
from pathlib import Path

from astermesh.server import create_mcp_server


def test_server_tools_and_resources(tmp_path: Path):
    # arrange: create a small codebase in tmp_path
    fpath = tmp_path / "service.py"
    fpath.write_text("""class UserService:
    def get_user(self, user_id):
        return {"id": user_id}

def main():
    srv = UserService()
    return srv.get_user(1)
""", encoding="utf-8")

    async def _run_async_test():
        server = create_mcp_server(tmp_path, watch=False)

        # act & assert: verify tools registration
        tools = await server.list_tools()
        tool_names = {t.name for t in tools}
        expected_tools = {
            "get_community",
            "get_neighbors",
            "get_impact_radius",
            "get_file_outline",
            "trace_path",
            "locate_symbol",
            "rebuild_graph",
        }
        assert expected_tools.issubset(tool_names)

        # act & assert: verify resources
        resources = await server.list_resources()
        resource_uris = {str(r.uri) for r in resources}
        assert "codebase://report" in resource_uris
        assert "codebase://communities" in resource_uris

        # act: read resources
        report_res = await server.read_resource("codebase://report")
        assert report_res is not None
        assert "Architecture Snapshot" in str(report_res)

        communities_res = await server.read_resource("codebase://communities")
        assert communities_res is not None
        assert "Discovered Architectural Communities" in str(communities_res)

        # act: call locate_symbol tool
        locate_res = await server.call_tool("locate_symbol", {"name": "UserService"})
        assert locate_res is not None
        assert len(locate_res.content) > 0

        # act: call file outline tool
        outline_res = await server.call_tool("get_file_outline", {"file_path": "service.py"})
        assert outline_res is not None
        assert len(outline_res.content) > 0

        # act: call rebuild_graph tool
        rebuild_res = await server.call_tool("rebuild_graph", {})
        assert rebuild_res is not None
        assert "rebuilt" in str(rebuild_res.content[0].text)

    asyncio.run(_run_async_test())
