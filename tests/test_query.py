import networkx as nx

from astermesh.graph.model import EdgeType, NodeType
from astermesh.graph.query import GraphQuery


def _build_test_graph() -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    # nodes
    g.add_node(
        "app.main",
        name="main",
        type=NodeType.FUNCTION.value,
        file_path="app/main.py",
        line_start=1,
        line_end=10,
    )
    g.add_node(
        "app.handler",
        name="handler",
        type=NodeType.FUNCTION.value,
        file_path="app/main.py",
        line_start=12,
        line_end=20,
    )
    g.add_node(
        "core.service",
        name="service",
        type=NodeType.CLASS.value,
        file_path="core/service.py",
        line_start=5,
        line_end=50,
    )
    g.add_node(
        "db.query",
        name="query",
        type=NodeType.FUNCTION.value,
        file_path="db/client.py",
        line_start=1,
        line_end=15,
    )

    # edges: main -> handler -> service -> query
    g.add_edge("app.main", "app.handler", type=EdgeType.CALLS.value)
    g.add_edge("app.handler", "core.service", type=EdgeType.CALLS.value)
    g.add_edge("core.service", "db.query", type=EdgeType.CALLS.value)
    return g


def test_query_resolve_and_locate():
    # arrange
    g = _build_test_graph()
    q = GraphQuery(g)

    # act & assert
    assert q.resolve_node_id("app.main") == "app.main"
    assert q.resolve_node_id("MAIN") == "app.main"
    assert q.resolve_node_id("non_existent") is None

    located = q.locate("service")
    assert len(located) == 1
    assert located[0]["id"] == "core.service"


def test_query_neighbors():
    # arrange
    g = _build_test_graph()
    q = GraphQuery(g)

    # act
    res = q.neighbors("handler", direction="both")

    # assert
    assert res["symbol"] == "app.handler"
    callers = [c["id"] for c in res["incoming_callers"]]
    callees = [c["id"] for c in res["outgoing_dependencies"]]
    assert "app.main" in callers
    assert "core.service" in callees


def test_query_impact_radius():
    # arrange
    g = _build_test_graph()
    q = GraphQuery(g)

    # act: impact of db.query
    impact = q.impact_radius("db.query", max_depth=3)

    # assert
    assert impact["target_symbol"] == "db.query"
    assert impact["total_affected_entities"] == 3
    assert "core/service.py" in impact["affected_files"]
    assert "app/main.py" in impact["affected_files"]


def test_query_file_outline():
    # arrange
    g = _build_test_graph()
    q = GraphQuery(g)

    # act
    outline = q.file_outline("app/main.py")

    # assert
    assert outline["total_entities"] == 2
    ids = [e["id"] for e in outline["outline"]]
    assert ids == ["app.main", "app.handler"]


def test_query_trace_path():
    # arrange
    g = _build_test_graph()
    q = GraphQuery(g)

    # act
    res = q.trace_path("app.main", "db.query")

    # assert
    assert res["path"] == ["app.main", "app.handler", "core.service", "db.query"]


# kiem thu query ca bien chu trinh, nguong rui ro va duong dan windows
def test_query_edge_cases_and_cycles():
    g = nx.MultiDiGraph()
    g.add_node(
        "src.app.A",
        name="A",
        type=NodeType.FUNCTION.value,
        file_path="src/app/main.py",
        line_start=10,
        line_end=20,
    )
    g.add_node(
        "src.app.B",
        name="B",
        type=NodeType.FUNCTION.value,
        file_path="src/app/main.py",
        line_start=1,
        line_end=8,
    )
    g.add_node(
        "isolated",
        name="isolated",
        type=NodeType.FUNCTION.value,
        file_path="src/other.py",
        line_start=1,
        line_end=2,
    )
    g.add_edge("src.app.A", "src.app.B", type=EdgeType.CALLS.value)
    g.add_edge("src.app.B", "src.app.A", type=EdgeType.CALLS.value)

    for i in range(4):
        cid = f"caller_{i}"
        g.add_node(
            cid,
            name=cid,
            type=NodeType.FUNCTION.value,
            file_path=f"src/c{i}.py",
            line_start=1,
            line_end=2,
        )
        g.add_edge(cid, "src.app.B", type=EdgeType.CALLS.value)

    q = GraphQuery(g)

    impact = q.impact_radius("B", max_depth=1)
    assert impact["risk_level"] == "MEDIUM"
    assert impact["total_affected_entities"] == 5

    assert "error" in q.neighbors("missing_sym")
    assert q.trace_path("src.app.A", "isolated")["path"] == []
    assert "error" in q.trace_path("src.app.A", "missing")

    outline = q.file_outline("src\\app\\main.py")
    assert [e["id"] for e in outline["outline"]] == ["src.app.B", "src.app.A"]
    assert "error" in q.file_outline("not_found.py")


# locate ranking and semantic impact filter
def test_locate_ranks_ast_above_containers_and_impact_skips_structural():
    g = nx.MultiDiGraph()
    for i in range(26):
        fpath = f"dir_{i}/auth_{i}.ts"
        g.add_node(fpath, name=f"auth_{i}.ts", type=NodeType.FILE.value, file_path=fpath)
    g.add_node("auth", name="auth", type=NodeType.FOLDER.value, file_path="auth")
    g.add_node("src/auth.ts", name="auth.ts", type=NodeType.FILE.value, file_path="src/auth.ts")
    g.add_node(
        "src.auth.AuthService",
        name="AuthService",
        type=NodeType.SERVICE.value,
        file_path="src/auth.ts",
        line_start=12,
        line_end=30,
    )
    g.add_node(
        "src.api.login",
        name="login",
        type=NodeType.FUNCTION.value,
        file_path="src/api.ts",
        line_start=1,
        line_end=5,
    )
    g.add_edge("auth", "src/auth.ts", type=EdgeType.CONTAINS.value)
    g.add_edge("src/auth.ts", "src.auth.AuthService", type=EdgeType.DEFINES.value)
    g.add_edge("src.api.login", "src.auth.AuthService", type=EdgeType.CALLS.value)

    q = GraphQuery(g)

    located = q.locate("auth")
    assert located[0]["id"] == "src.auth.AuthService"
    assert located[0]["line_start"] == 12
    ast_only = q.locate("auth", include_containers=False)
    assert all(r["type"] not in {NodeType.FILE.value, NodeType.FOLDER.value} for r in ast_only)

    impact = q.impact_radius("src.auth.AuthService", max_depth=3)
    assert impact["total_affected_entities"] == 1
    assert [item["id"] for item in impact["impact_by_depth"][1]] == ["src.api.login"]
    assert impact["affected_files"] == ["src/api.ts"]
