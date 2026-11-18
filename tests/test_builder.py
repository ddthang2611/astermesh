from pathlib import Path

from astermesh.graph.builder import GraphBuilder
from astermesh.graph.model import EdgeType, NodeType
from astermesh.graph.query import GraphQuery
from astermesh.graph.symbol_index import SymbolIndex
from astermesh.parser.types import FileParseResult, RawEntity


# kiem thu dang ky va huy chi muc ky hieu
def test_symbol_index_register_unregister_and_clear():
    idx = SymbolIndex()
    ent = RawEntity(
        name="run",
        fqn="pkg.mod.Engine.run",
        kind=NodeType.FUNCTION.value,
        line_start=1,
        line_end=5,
    )
    parsed = FileParseResult(
        file_path="pkg/mod.py",
        module_fqn="pkg.mod",
        entities=[ent],
        language="python",
    )

    idx.register_file("pkg/mod.py", parsed, {"pkg/mod.py", "pkg.mod.Engine.run"})

    assert idx.symbol_table["pkg.mod"] == "pkg/mod.py"
    assert idx.symbol_table["pkg.mod.Engine.run"] == "pkg.mod.Engine.run"
    assert "pkg.mod.Engine.run" in idx.short_names["run"]
    assert "pkg.mod.Engine.run" in idx.suffix_table["Engine.run"]
    assert "pkg.mod.Engine.run" in idx.suffix_table["mod.Engine.run"]

    removed = idx.unregister_file("pkg/mod.py")
    assert removed == {"pkg/mod.py", "pkg.mod.Engine.run"}
    assert "pkg.mod.Engine.run" not in idx.symbol_table
    assert not idx.short_names["run"]
    assert not idx.suffix_table["Engine.run"]

    idx.register_file("pkg/mod.py", parsed, {"pkg/mod.py"})
    idx.clear()
    assert not idx.symbol_table and not idx.parsed_files and not idx.file_nodes


# kiem thu xay dung do thi day du va ke thua
def test_build_full_and_edges(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text(
        "class BaseService:\n    def execute(self):\n        return 42\n",
        encoding="utf-8",
    )
    (pkg / "b.py").write_text(
        "from pkg.a import BaseService\n\nclass ExtendedService(BaseService):\n"
        "    def run(self):\n        return self.execute()\n",
        encoding="utf-8",
    )

    graph = GraphBuilder(tmp_path).build_full()

    assert graph.has_node("pkg")
    assert graph.has_node("pkg/a.py")
    assert graph.has_node("pkg/b.py")
    assert graph.has_node("pkg.a.BaseService")
    assert graph.has_node("pkg.b.ExtendedService")

    edges = list(graph.edges("pkg.b.ExtendedService", data=True))
    inherits = [e for e in edges if e[2].get("type") == EdgeType.INHERITS.value]
    assert len(inherits) == 1
    assert inherits[0][1] == "pkg.a.BaseService"


# kiem thu cap nhat vi sai va xoa tep
def test_incremental_update_and_removal(tmp_path: Path):
    fpath = tmp_path / "worker.py"
    fpath.write_text("def do_work(): pass\n", encoding="utf-8")
    builder = GraphBuilder(tmp_path)
    builder.build_full()
    assert builder.graph.has_node("worker.do_work")

    fpath.write_text("def do_new_work(): pass\n", encoding="utf-8")
    builder.update_file(fpath)
    assert not builder.graph.has_node("worker.do_work")
    assert builder.graph.has_node("worker.do_new_work")

    fpath.unlink()
    builder.remove_path("worker.py")
    assert not builder.graph.has_node("worker.do_new_work")
    assert not builder.graph.has_node("worker.py")


# kiem thu tep ve tinh, hau to va canh du phong asset
def test_satellite_indexing_suffix_cleanup_and_asset_fallback(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nlicense = { text = "MIT" }\n',
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Demo\n## Usage\n", encoding="utf-8")
    (tmp_path / "logo.svg").write_text("<svg/>", encoding="utf-8")
    app_file = tmp_path / "App.tsx"
    app_file.write_text(
        "import unusedLogo from './logo.svg';\n"
        "export class Shell {\n    renderShell() { return null; }\n}\n",
        encoding="utf-8",
    )

    builder = GraphBuilder(tmp_path)
    g = builder.build_full()
    q = GraphQuery(g)

    lic_matches = q.locate("license")
    assert any(m["file_path"] == "pyproject.toml" and m["line_start"] == 3 for m in lic_matches)
    assert q.file_outline("README.md")["total_entities"] == 3
    assert q.resolve_node_id("Shell.renderShell") == "App.Shell.renderShell"
    assert g.nodes["logo.svg"]["type"] == NodeType.ASSET.value

    file_edges = [(v, d["type"]) for _, v, d in g.out_edges("App.tsx", data=True)]
    assert ("logo.svg", EdgeType.IMPORTS.value) in file_edges

    app_file.unlink()
    builder.remove_path("App.tsx")
    assert "App.Shell.renderShell" not in builder._suffix_table.get("Shell.renderShell", set())


def test_symlink_pointing_outside_root_is_skipped(tmp_path: Path):
    outside = tmp_path / "shared"
    outside.mkdir()
    (outside / "util.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")
    link = proj / "util.py"
    link.symlink_to(outside / "util.py")

    builder = GraphBuilder(proj)
    g = builder.build_full()

    assert g.has_node("main.main")
    assert not g.has_node("util.py")
    builder.update_file(link)  # watcher path must not raise either
    assert not g.has_node("util.py")
