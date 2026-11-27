import builtins
from pathlib import Path
from typing import Any

import pytest

from astermesh.graph.builder import GraphBuilder
from astermesh.graph.model import EdgeType, NodeType
from astermesh.graph.symbol_index import SymbolIndex
from astermesh.languages.java.plugin import JavaLanguagePlugin
from astermesh.languages.registry import default_registry
from astermesh.parser.types import FileParseResult, RawEntity


# family isolation and 7-index cleanup
def test_symbol_index_family_isolation_and_complete_cleanup() -> None:
    # arrange
    idx = SymbolIndex()

    py_res = FileParseResult(
        file_path="py/service.py",
        module_fqn="py.service",
        language="python",
        entities=[
            RawEntity(
                name="Service",
                fqn="py.service.Service",
                kind=NodeType.CLASS.value,
                line_start=1,
                line_end=10,
                bases=["BaseService"],
            ),
            RawEntity(
                name="getById",
                fqn="py.service.Service.getById",
                kind=NodeType.FUNCTION.value,
                line_start=2,
                line_end=4,
                parent_fqn="py.service.Service",
                is_method=True,
            ),
        ],
    )
    js_res = FileParseResult(
        file_path="fe/service.ts",
        module_fqn="fe.service",
        language="typescript",
        entities=[
            RawEntity(
                name="Service",
                fqn="fe.service.Service",
                kind=NodeType.CLASS.value,
                line_start=1,
                line_end=10,
                bases=["AbstractService"],
            ),
            RawEntity(
                name="getById",
                fqn="fe.service.Service.getById",
                kind=NodeType.FUNCTION.value,
                line_start=2,
                line_end=4,
                parent_fqn="fe.service.Service",
                is_method=True,
            ),
        ],
    )
    java_res = FileParseResult(
        file_path="be/Service.java",
        module_fqn="be.Service",
        language="java",
        language_family="java",
        namespace_fqn="com.acme",
        type_aliases={"ImmutableOrder": "com.acme.Order"},
        entities=[
            RawEntity(
                name="Service",
                fqn="com.acme.Service",
                kind=NodeType.SERVICE.value,
                line_start=5,
                line_end=25,
                parent_fqn="be.Service",
                bases=["BaseRepo"],
                language_family="java",
                namespace_fqn="com.acme",
            ),
            RawEntity(
                name="getById",
                fqn="com.acme.Service.getById#1",
                kind=NodeType.FUNCTION.value,
                line_start=6,
                line_end=10,
                parent_fqn="com.acme.Service",
                is_method=True,
                language_family="java",
                namespace_fqn="com.acme",
            ),
            RawEntity(
                name="getById",
                fqn="com.acme.Service.getById#2",
                kind=NodeType.FUNCTION.value,
                line_start=12,
                line_end=16,
                parent_fqn="com.acme.Service",
                is_method=True,
                language_family="java",
                namespace_fqn="com.acme",
            ),
        ],
    )
    sat_res = FileParseResult(
        file_path="pom.xml",
        module_fqn="pom_sat",
        language="satellite",
        entities=[
            RawEntity(
                name="demo",
                fqn="pom_sat.artifactId.demo",
                kind=NodeType.CONFIG.value,
                line_start=3,
                line_end=3,
                parent_fqn="pom_sat",
            )
        ],
    )

    # act
    idx.register_file(
        "py/service.py",
        py_res,
        {"py/service.py", "py.service.Service", "py.service.Service.getById"},
    )
    idx.register_file(
        "fe/service.ts",
        js_res,
        {"fe/service.ts", "fe.service.Service", "fe.service.Service.getById"},
    )
    idx.register_file(
        "be/Service.java",
        java_res,
        {
            "be/Service.java",
            "com.acme.Service",
            "com.acme.Service.getById#1",
            "com.acme.Service.getById#2",
        },
    )
    idx.register_file("pom.xml", sat_res, {"pom.xml", "pom_sat.artifactId.demo"})

    # assert: family isolation across lookup tables
    assert idx.get_short_names("getById", family="python") == {"py.service.Service.getById"}
    assert idx.get_short_names("getById", family="js_ts_web") == {"fe.service.Service.getById"}
    assert idx.get_short_names("getById", family="java") == {
        "com.acme.Service.getById#1",
        "com.acme.Service.getById#2",
    }
    assert idx.get_short_names("demo", family="satellite") == set()

    assert idx.get_suffix_matches("Service.getById", family="python") == {
        "py.service.Service.getById"
    }
    assert idx.get_suffix_matches("Service.getById", family="js_ts_web") == {
        "fe.service.Service.getById"
    }
    assert idx.get_suffix_matches("Service", family="java") == {"com.acme.Service"}
    assert "pom_sat" in idx.get_symbol_table("satellite")

    assert idx.get_namespace_members("java", "com.acme", "Service") == {"com.acme.Service"}
    assert idx.get_overloads("com.acme.Service", "getById") == [
        "com.acme.Service.getById#1",
        "com.acme.Service.getById#2",
    ]
    assert idx.type_hierarchy["com.acme.Service"] == {"BaseRepo"}
    assert idx.get_type_alias("java", "ImmutableOrder") == "com.acme.Order"

    # act: unregister java file only
    removed_java = idx.unregister_file("be/Service.java")
    assert "com.acme.Service" in removed_java
    assert idx.get_symbol_table("java") == {}
    assert idx.get_short_names("getById", family="java") == set()
    assert idx.get_suffix_matches("Service", family="java") == set()
    assert idx.get_namespace_members("java", "com.acme", "Service") == set()
    assert idx.get_overloads("com.acme.Service", "getById") == []
    assert "com.acme.Service" not in idx.type_hierarchy
    assert idx.get_type_alias("java", "ImmutableOrder") is None

    # assert: other families remain intact
    assert idx.get_short_names("getById", family="python") == {"py.service.Service.getById"}
    assert idx.get_short_names("getById", family="js_ts_web") == {"fe.service.Service.getById"}
    assert "pom_sat" in idx.get_symbol_table("satellite")

    # act: unregister remaining files
    idx.unregister_file("py/service.py")
    idx.unregister_file("fe/service.ts")
    idx.unregister_file("pom.xml")

    # assert: all 7 indices reclaimed cleanly
    assert idx.symbol_table == {}
    assert idx.parsed_files == {}
    assert idx.file_nodes == {}
    assert idx.family_symbol_tables == {}
    assert idx.family_short_names == {}
    assert idx.family_suffix_tables == {}
    assert idx.namespace_members == {}
    assert idx.overload_groups == {}
    assert idx.type_hierarchy == {}
    assert idx.type_aliases == {}
    assert idx._file_keys == {}
    assert all(not v for v in idx.short_names.values())
    assert all(not v for v in idx.suffix_table.values())


# zero cross-language edges between js and java
def test_zero_cross_language_edges_between_js_and_java(tmp_path: Path) -> None:
    # arrange: synthetic bilingual repo with identical method names
    fe_dir = tmp_path / "frontend" / "src"
    be_dir = tmp_path / "backend" / "src" / "main" / "java" / "com" / "acme"
    fe_dir.mkdir(parents=True)
    be_dir.mkdir(parents=True)

    (fe_dir / "api.ts").write_text(
        """export function getById(id: string) { return id; }
export function findAll() { return []; }
export function save(item: unknown) { return item; }
export function register(name: string) { return name; }
""",
        encoding="utf-8",
    )
    (fe_dir / "page.ts").write_text(
        """import { getById, findAll } from './api';

export function loadPage() {
    const a = getById('1');
    const b = findAll();
    save(a);
    register('ui');
    return [a, b];
}
""",
        encoding="utf-8",
    )

    (be_dir / "Repo.java").write_text(
        """package com.acme;

public class Repo {
    public String getById(String id) { return id; }
    public String findAll() { return "all"; }
    public void save(String item) {}
    public static void register(String name) {}
}
""",
        encoding="utf-8",
    )
    (be_dir / "Controller.java").write_text(
        """package com.acme;

public class Controller {
    private final Repo repo = new Repo();

    public void handle() {
        repo.getById("1");
        repo.findAll();
        repo.save("x");
        Repo.register("be");
    }
}
""",
        encoding="utf-8",
    )

    # act
    graph = GraphBuilder(tmp_path).build_full()

    # assert: both families indexed with intra-family call edges
    js_nodes = {n for n, d in graph.nodes(data=True) if d.get("language_family") == "js_ts_web"}
    java_nodes = {n for n, d in graph.nodes(data=True) if d.get("language_family") == "java"}
    assert js_nodes
    assert java_nodes

    assert graph.has_edge("frontend.src.page.loadPage", "frontend.src.api.getById")
    assert graph.has_edge("frontend.src.page.loadPage", "frontend.src.api.findAll")
    assert graph.has_edge("com.acme.Controller.handle", "com.acme.Repo.getById")
    assert graph.has_edge("com.acme.Controller.handle", "com.acme.Repo.findAll")
    assert graph.has_edge("com.acme.Controller.handle", "com.acme.Repo.save")
    assert graph.has_edge("com.acme.Controller.handle", "com.acme.Repo.register")

    cross_edges = [
        (u, v, ed.get("type"))
        for u, v, ed in graph.edges(data=True)
        if (u in js_nodes and v in java_nodes) or (u in java_nodes and v in js_nodes)
    ]
    assert cross_edges == []

    # verify no CALLS/REFERENCES/INJECTS/INHERITS leak between families
    semantic_types = {
        EdgeType.CALLS.value,
        EdgeType.REFERENCES.value,
        EdgeType.INJECTS.value,
        EdgeType.INHERITS.value,
        EdgeType.IMPORTS.value,
    }
    assert all(
        (graph.nodes[u].get("language_family"), graph.nodes[v].get("language_family"))
        not in {("js_ts_web", "java"), ("java", "js_ts_web")}
        for u, v, ed in graph.edges(data=True)
        if ed.get("type") in semantic_types
    )


# graceful degradation without tree-sitter-java
def test_graceful_degradation_when_java_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # arrange
    (tmp_path / "App.java").write_text("public class App {}\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("def run(): return 1\n", encoding="utf-8")
    (tmp_path / "index.ts").write_text("export const x = 1;\n", encoding="utf-8")

    monkeypatch.setattr(JavaLanguagePlugin, "is_available", lambda self: False)

    # act
    reg = default_registry()
    graph = GraphBuilder(tmp_path, registry=reg).build_full()

    # assert: java skipped without error while py and ts remain indexed
    assert ".java" not in reg.supported_extensions
    assert not graph.has_node("App.java")
    assert graph.has_node("app.run")
    assert graph.has_node("index.x")

    # simulate direct ImportError on tree_sitter_java
    real_import = builtins.__import__

    def _fail_ts_java(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "tree_sitter_java":
            raise ImportError("missing tree_sitter_java")
        return real_import(name, *args, **kwargs)

    monkeypatch.undo()
    monkeypatch.setattr(builtins, "__import__", _fail_ts_java)
    plugin = JavaLanguagePlugin()
    assert plugin.is_available() is False
    assert plugin.parse_file(b"class A {}", "A.java", tmp_path) is None
