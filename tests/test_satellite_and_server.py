import asyncio
import json
from pathlib import Path

from astermesh.graph.builder import GraphBuilder
from astermesh.graph.query import GraphQuery
from astermesh.server import create_mcp_server


def test_satellite_files_and_suffix_lookup(tmp_path: Path):
    # arrange
    (tmp_path / "pyproject.toml").write_text(
        """[project]
name = "demo"
version = "1.0.0"
license = { text = "MIT" }
""",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        """# Demo Project
## Installation
Run pip install.
""",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        """class Engine:
    def run(self):
        return 1
""",
        encoding="utf-8",
    )

    builder = GraphBuilder(tmp_path)
    g = builder.build_full()
    q = GraphQuery(g)

    # assert: satellite configs indexed with line coordinates
    lic_matches = q.locate("license")
    assert any(m["file_path"] == "pyproject.toml" and m["line_start"] == 4 for m in lic_matches)

    readme_outline = q.file_outline("README.md")
    assert readme_outline["total_entities"] >= 2

    # assert: Class.method dot-path suffix resolves cleanly
    assert q.resolve_node_id("Engine.run") == "app.Engine.run"


def test_mcp_descriptions_snippet_and_context(tmp_path: Path):
    # arrange
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'test'\nlicense = 'MIT'\n",
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")

    async def _run():
        server = create_mcp_server(tmp_path, watch=False)

        # verify non-empty descriptions across all 8 tools
        tools = await server.list_tools()
        assert len(tools) == 8
        for t in tools:
            assert t.description and len(t.description.strip()) > 15

        # verify codebase://context returns ACN format
        ctx = await server.read_resource("codebase://context")
        assert "#ACN1" in str(ctx)

        # verify read_file_snippet returns targeted lines
        snippet_res = await server.call_tool(
            "read_file_snippet", {"file_path": "pyproject.toml", "start_line": 2, "end_line": 3}
        )
        payload = json.loads(snippet_res.content[0].text)
        assert "2: name = 'test'" in payload["content"]
        assert "3: license = 'MIT'" in payload["content"]

        # verify path traversal is rejected
        bad_res = await server.call_tool(
            "read_file_snippet", {"file_path": "../../etc/passwd"}
        )
        assert "error" in json.loads(bad_res.content[0].text)

    asyncio.run(_run())
