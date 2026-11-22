import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import astermesh
from astermesh.server import create_mcp_server


def _tool_text(res: Any) -> str:
    return str(res.content[0].text) if res.content else ""


# kiem thu lazy export tren package goc
def test_package_version_and_lazy_getattr() -> None:
    assert astermesh.__version__ == "1.3.4"
    assert astermesh.create_mcp_server is create_mcp_server
    with pytest.raises(AttributeError):
        _ = astermesh.non_existent_symbol


# kiem thu toan dien cong cu va tai nguyen mcp server
def test_server_tools_resources_and_community_wire(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'test'\nlicense = 'MIT'\n",
        encoding="utf-8",
    )
    (tmp_path / "service.py").write_text(
        """class UserService:
    def get_user(self, user_id):
        return {"id": user_id}

def main():
    srv = UserService()
    return srv.get_user(1)
""",
        encoding="utf-8",
    )

    async def _run() -> None:
        server = create_mcp_server(tmp_path, watch=False, context_max_tokens=300)

        tools = await server.list_tools()
        assert len(tools) == 9
        for t in tools:
            assert t.description and len(t.description.strip()) > 15

        resources = await server.list_resources()
        uris = {str(r.uri) for r in resources}
        assert {"codebase://context", "codebase://report", "codebase://communities"}.issubset(uris)

        ctx = await server.read_resource("codebase://context")
        assert "#ACN2" in str(ctx)
        assert "Architecture Snapshot" in str(await server.read_resource("codebase://report"))
        assert "Discovered Architectural Communities" in str(
            await server.read_resource("codebase://communities")
        )

        locate_res: Any = await server.call_tool("locate_symbol", {"name": "UserService"})
        assert locate_res is not None and len(locate_res.content) > 0

        outline_res: Any = await server.call_tool("get_file_outline", {"file_path": "service.py"})
        assert outline_res is not None and len(outline_res.content) > 0

        comm_ok = await server.call_tool("get_community", {"name": "C1"})
        assert json.loads(_tool_text(comm_ok))["id"] == "C1"

        comm_err = await server.call_tool("get_community", {"name": "Missing"})
        assert "error" in json.loads(_tool_text(comm_err))

        wire_ok = await server.call_tool("get_community_wire", {"name": "C1", "max_tokens": 200})
        assert "#ACN2 " in _tool_text(wire_ok)
        assert "@C1 " in _tool_text(wire_ok)

        wire_err = await server.call_tool("get_community_wire", {"name": "Missing"})
        assert "Error:" in _tool_text(wire_err)

        snippet = await server.call_tool(
            "read_file_snippet",
            {"file_path": "pyproject.toml", "start_line": 2, "end_line": 3},
        )
        payload = json.loads(_tool_text(snippet))
        assert "2: name = 'test'" in payload["content"]
        assert "3: license = 'MIT'" in payload["content"]

        bad_snippet = await server.call_tool("read_file_snippet", {"file_path": "../../etc/passwd"})
        assert "error" in json.loads(_tool_text(bad_snippet))

        rebuilt = await server.call_tool("rebuild_graph", {})
        assert json.loads(_tool_text(rebuilt))["status"] == "rebuilt"

    asyncio.run(_run())


# summarize singletons and server exclusions
def test_server_communities_summarizes_singletons_and_excludes(tmp_path: Path) -> None:
    (tmp_path / "resources").mkdir()
    (tmp_path / "resources/ignored.py").write_text("def noise_fn(): pass\n", encoding="utf-8")
    (tmp_path / "mod.py").write_text(
        "def iso_a(): return 1\n"
        "def iso_b(): return 2\n"
        "def caller(): return callee()\n"
        "def callee(): return 3\n",
        encoding="utf-8",
    )

    async def _run() -> None:
        server = create_mcp_server(tmp_path, watch=False, exclude_patterns=["resources/**"])
        comms_text = str(await server.read_resource("codebase://communities"))
        assert "Isolated Singletons (size=1):" in comms_text
        assert "iso_a" not in comms_text
        assert "iso_b" not in comms_text

        locate_noise: Any = await server.call_tool("locate_symbol", {"name": "noise_fn"})
        assert "noise_fn" not in _tool_text(locate_noise)

    asyncio.run(_run())


# kiem thu chuyen doi da project dong khong can hardcode root_path
def test_server_dynamic_multi_workspace_switching(tmp_path: Path) -> None:
    proj_a = tmp_path / "proj_a"
    proj_b = tmp_path / "proj_b"
    proj_a.mkdir()
    proj_b.mkdir()
    (proj_a / "a.py").write_text("def func_alpha(): return 'A'\n", encoding="utf-8")
    (proj_b / "b.py").write_text("def func_beta(): return 'B'\n", encoding="utf-8")

    async def _run() -> None:
        server = create_mcp_server(root_path=None, watch=False)

        res_a: Any = await server.call_tool(
            "locate_symbol",
            {"name": "func_alpha", "workspace_path": str(proj_a)},
        )
        assert "func_alpha" in _tool_text(res_a)

        res_b: Any = await server.call_tool(
            "locate_symbol",
            {"name": "func_beta", "workspace_path": str(proj_b)},
        )
        assert "func_beta" in _tool_text(res_b)

        # Active workspace is now proj_b even when workspace_path is omitted
        res_b_active: Any = await server.call_tool(
            "locate_symbol",
            {"name": "func_beta"},
        )
        assert "func_beta" in _tool_text(res_b_active)

    asyncio.run(_run())

