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


# kiem thu bo qua js bien dich canh ts
def test_js_emitted_next_to_ts_source_does_not_shadow_it(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "foo.ts").write_text(
        "export function helper(a: number) {\n  return a\n}\n", encoding="utf-8"
    )
    js_file = src / "foo.js"
    js_file.write_text("\n\nexport function helper(a) {\n  return a\n}\n", encoding="utf-8")
    (src / "app.ts").write_text(
        "import { helper } from './foo.js';\nexport function run() { return helper(1); }\n",
        encoding="utf-8",
    )

    builder = GraphBuilder(tmp_path)
    g = builder.build_full()

    assert g.nodes["src.foo.helper"]["file_path"] == "src/foo.ts"
    assert not g.has_node("src/foo.js")
    run_edges = {(v, d["type"]) for _, v, d in g.out_edges("src.app.run", data=True)}
    assert ("src.foo.helper", EdgeType.CALLS.value) in run_edges

    js_file.unlink()
    builder.update_file(js_file)  # watcher event for emitted file
    assert g.has_node("src.foo.helper")


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
    assert builder.ensure_folder(outside) == "."


# skip duplicate internal workspace symlinks
def test_internal_symlink_pointing_inside_root_is_skipped(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "real.py").write_text("def real_fn():\n    return 1\n", encoding="utf-8")
    alias = src / "alias.py"
    alias.symlink_to(src / "real.py")

    builder = GraphBuilder(tmp_path)
    g = builder.build_full()

    assert g.has_node("src/real.py")
    assert g.has_node("src.real.real_fn")
    assert not g.has_node("src/alias.py")
    assert not g.has_node("src.alias.real_fn")

    builder.update_file(alias)
    assert g.has_node("src/real.py")
    assert g.has_node("src.real.real_fn")


# reindex fallback js after ts delete
def test_deleting_ts_sibling_reindexes_existing_js_fallback(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    ts_file = src / "foo.ts"
    ts_file.write_text("export function fromTs() { return 1; }\n", encoding="utf-8")
    (src / "foo.js").write_text("export function fromJs() { return 2; }\n", encoding="utf-8")

    builder = GraphBuilder(tmp_path)
    g = builder.build_full()
    assert g.has_node("src.foo.fromTs")
    assert not g.has_node("src/foo.js")

    ts_file.unlink()
    builder.update_file(ts_file)
    assert not g.has_node("src/foo.ts")
    assert not g.has_node("src.foo.fromTs")
    assert g.has_node("src/foo.js")
    assert g.has_node("src.foo.fromJs")


# update_file relinks incoming cross-file edges
def test_update_file_relinks_incoming_cross_file_dependents(tmp_path: Path) -> None:
    # arrange
    java_dir = tmp_path / "src" / "main" / "java" / "com" / "acme"
    web_dir = tmp_path / "web" / "src"
    java_dir.mkdir(parents=True)
    web_dir.mkdir(parents=True)

    user_svc = java_dir / "UserService.java"
    user_svc.write_text(
        """package com.acme;

public class UserService {
    public String getById(long id) { return "u"; }
    public void save(String name) {}
}
""",
        encoding="utf-8",
    )
    (java_dir / "Main.java").write_text(
        """package com.acme;

public class Main {
    private final UserService svc = new UserService();
    public void run() {
        svc.getById(1L);
        svc.save("alice");
    }
}
""",
        encoding="utf-8",
    )

    util_js = web_dir / "util.js"
    util_js.write_text("export function compute(x) { return x; }\n", encoding="utf-8")
    (web_dir / "app.js").write_text(
        "import { compute } from './util.js';\nexport function boot() { return compute(1); }\n",
        encoding="utf-8",
    )

    builder = GraphBuilder(tmp_path)
    g = builder.build_full()
    assert g.has_edge("com.acme.Main.run", "com.acme.UserService.getById")
    assert g.has_edge("com.acme.Main.run", "com.acme.UserService.save")
    assert g.has_edge("web.src.app.boot", "web.src.util.compute")

    # act: incremental update on callee files
    builder.update_file(user_svc)
    builder.update_file(util_js)

    # assert: incoming cross-file edges preserved
    assert g.has_edge("com.acme.Main.run", "com.acme.UserService.getById")
    assert g.has_edge("com.acme.Main.run", "com.acme.UserService.save")
    assert g.has_edge("web.src.app.boot", "web.src.util.compute")


# update_file relative path and pruned java relink
def test_update_file_accepts_relative_path_and_relinks_pruned_java(
    tmp_path: Path, monkeypatch
) -> None:
    # arrange
    java_dir = tmp_path / "src" / "main" / "java" / "com" / "acme"
    java_dir.mkdir(parents=True)
    svc = java_dir / "Svc.java"
    svc.write_text(
        """package com.acme;
public class Svc {
    public void ping() {}
}
""",
        encoding="utf-8",
    )
    (java_dir / "App.java").write_text(
        """package com.acme;
public class App {
    private final Svc svc = new Svc();
    public void run() { svc.ping(); }
}
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path.parent)

    builder = GraphBuilder(tmp_path)
    g = builder.build_full()
    assert builder._index.parsed_files["src/main/java/com/acme/App.java"].link_pruned is True
    assert g.has_edge("com.acme.App.run", "com.acme.Svc.ping")

    # act: pass workspace-relative path from different cwd
    svc.write_text(
        """package com.acme;
public class Svc {
    public void ping() {}
    public void pong() {}
}
""",
        encoding="utf-8",
    )
    builder.update_file(Path("src/main/java/com/acme/Svc.java"))

    # assert
    assert g.has_node("com.acme.Svc.pong")
    assert g.has_edge("com.acme.App.run", "com.acme.Svc.ping")


# disambiguate colliding java fqns across modules
def test_builder_disambiguates_colliding_java_fqns_across_modules(tmp_path: Path) -> None:
    # arrange
    mod_a = tmp_path / "mod-a" / "src" / "test" / "java" / "com" / "acme"
    mod_b = tmp_path / "mod-b" / "src" / "test" / "java" / "com" / "acme"
    mod_a.mkdir(parents=True)
    mod_b.mkdir(parents=True)

    (mod_a / "DupTest.java").write_text(
        """package com.acme;
public class DupTest {
    private int count;
    public void checkA() {}
}
""",
        encoding="utf-8",
    )
    (mod_b / "DupTest.java").write_text(
        """package com.acme;
public class DupTest {
    private int count;
    public void checkB() {}
}
""",
        encoding="utf-8",
    )

    # act
    g = GraphBuilder(tmp_path).build_full()

    # assert: both classes and members survive
    dup_classes = [
        nid
        for nid, d in g.nodes(data=True)
        if d.get("name") == "DupTest" and d.get("type") == "class"
    ]
    assert len(dup_classes) == 2
    assert any(g.nodes[nid].get("name") == "checkA" for nid in g.nodes)
    assert any(g.nodes[nid].get("name") == "checkB" for nid in g.nodes)

