from pathlib import Path

from astermesh.graph.builder import GraphBuilder, is_ignored_dir, is_ignored_file
from astermesh.graph.model import EdgeType, NodeType


def test_is_ignored_dir():
    # assert
    assert is_ignored_dir(".git")
    assert is_ignored_dir("foo/.venv/bar")
    assert not is_ignored_dir("src/astermesh")
    assert not is_ignored_dir(".")


def test_build_full_and_edges(tmp_path: Path):
    # arrange
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    mod_a = pkg / "a.py"
    mod_b = pkg / "b.py"

    mod_a.write_text("""class BaseService:
    def execute(self):
        return 42
""", encoding="utf-8")

    mod_b.write_text("""from pkg.a import BaseService

class ExtendedService(BaseService):
    def run(self):
        return self.execute()
""", encoding="utf-8")

    builder = GraphBuilder(tmp_path)

    # act
    graph = builder.build_full()

    # assert
    assert graph.has_node("pkg")
    assert graph.has_node("pkg/a.py")
    assert graph.has_node("pkg/b.py")
    assert graph.has_node("pkg.a.BaseService")
    assert graph.has_node("pkg.b.ExtendedService")

    # verify inheritance edge
    edges = list(graph.edges("pkg.b.ExtendedService", data=True))
    inherits = [e for e in edges if e[2].get("type") == EdgeType.INHERITS.value]
    assert len(inherits) == 1
    assert inherits[0][1] == "pkg.a.BaseService"


def test_incremental_update_and_removal(tmp_path: Path):
    # arrange
    fpath = tmp_path / "worker.py"
    fpath.write_text("def do_work(): pass\n", encoding="utf-8")
    builder = GraphBuilder(tmp_path)
    builder.build_full()
    assert builder.graph.has_node("worker.do_work")

    # act: update file
    fpath.write_text("def do_new_work(): pass\n", encoding="utf-8")
    builder.update_file(fpath)

    # assert: old node removed, new node indexed
    assert not builder.graph.has_node("worker.do_work")
    assert builder.graph.has_node("worker.do_new_work")

    # act: remove file
    fpath.unlink()
    builder.remove_path("worker.py")

    # assert: all nodes from file removed
    assert not builder.graph.has_node("worker.do_new_work")
    assert not builder.graph.has_node("worker.py")


# kiem thu bo loc tep bo qua, xoa suffix va canh du phong asset
def test_builder_ignored_files_suffix_cleanup_and_unused_asset_fallback(tmp_path: Path):
    assert is_ignored_file("vendor.min.js")
    assert is_ignored_file("app.bundle.js")
    assert is_ignored_file("app.js.map")
    assert not is_ignored_file("App.tsx")

    (tmp_path / "vendor.min.js").write_text("function a(){}", encoding="utf-8")
    (tmp_path / "logo.svg").write_text("<svg/>", encoding="utf-8")
    app_file = tmp_path / "App.tsx"
    app_file.write_text(
        """import unusedLogo from './logo.svg';
export class Shell {
    renderShell() { return null; }
}
""",
        encoding="utf-8",
    )

    builder = GraphBuilder(tmp_path)
    g = builder.build_full()

    assert not g.has_node("vendor.min.js")
    assert g.has_node("logo.svg")
    assert g.nodes["logo.svg"]["type"] == NodeType.ASSET.value
    assert "App.Shell.renderShell" in builder._suffix_table.get("Shell.renderShell", set())

    file_edges = [(v, d["type"]) for _, v, d in g.out_edges("App.tsx", data=True)]
    assert ("logo.svg", EdgeType.IMPORTS.value) in file_edges

    app_file.unlink()
    builder.remove_path("App.tsx")
    assert "App.Shell.renderShell" not in builder._suffix_table.get("Shell.renderShell", set())

    min_js = tmp_path / "extra.min.js"
    min_js.write_text("function x(){}", encoding="utf-8")
    builder.update_file(min_js)
    assert not g.has_node("extra.min.js")
