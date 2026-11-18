from astermesh.graph.model import (
    CONTAINER_NODE_TYPES,
    EDGE_WEIGHTS,
    EXCLUDED_FILE_TYPES,
    LOGIC_NODE_TYPES,
    NON_ENTITY_NODE_TYPES,
    SEMANTIC_IMPACT_EDGES,
    WIRE_EDGE_TYPES,
    CommunityInfo,
    EdgeType,
    EntityNode,
    NodeType,
)


# kiem thu tuan tu hoa thuc the
def test_entity_node_serialization() -> None:
    node = EntityNode(
        id="pkg.mod.MyClass",
        name="MyClass",
        node_type=NodeType.CLASS,
        file_path="pkg/mod.py",
        line_start=10,
        line_end=50,
        parameters=["x", "y"],
        is_async=False,
        is_method=False,
        community_id="C1",
    )

    data = node.to_dict()

    assert data["name"] == "MyClass"
    assert data["type"] == NodeType.CLASS.value
    assert data["file_path"] == "pkg/mod.py"
    assert data["line_start"] == 10
    assert data["line_end"] == 50
    assert data["parameters"] == ["x", "y"]
    assert data["community_id"] == "C1"


# kiem thu khoi tao thong tin cum
def test_community_info_creation() -> None:
    info = CommunityInfo(
        id="C0",
        name="engine_cluster",
        size=3,
        god_node="core.engine",
        members=["core.engine", "core.worker", "core.task"],
    )

    assert info.id == "C0"
    assert info.name == "engine_cluster"
    assert info.god_node == "core.engine"
    assert len(info.members) == 3


# kiem thu cac loai canh co ban
def test_edge_types() -> None:
    assert EdgeType.CONTAINS.value == "CONTAINS"
    assert EdgeType.DEFINES.value == "DEFINES"
    assert EdgeType.IMPORTS.value == "IMPORTS"
    assert EdgeType.CALLS.value == "CALLS"
    assert EdgeType.INHERITS.value == "INHERITS"
    assert EdgeType.REFERENCES.value == "REFERENCES"


# kiem thu schema fe, trong so va tap hang so
def test_fe_node_edge_weights_and_constant_sets() -> None:
    assert NodeType.COMPONENT.value == "component"
    assert NodeType.HOOK.value == "hook"
    assert NodeType.SERVICE.value == "service"
    assert NodeType.MODULE.value == "module"
    assert NodeType.ASSET.value == "asset"
    assert NodeType.CONFIG.value == "config"
    assert NodeType.DOC.value == "doc"

    assert EdgeType.RENDERS.value == "RENDERS"
    assert EdgeType.INJECTS.value == "INJECTS"
    assert EdgeType.PROVIDES.value == "PROVIDES"

    assert EDGE_WEIGHTS[EdgeType.DEFINES.value] == 3.0
    assert EDGE_WEIGHTS[EdgeType.INJECTS.value] == 2.5
    assert EDGE_WEIGHTS[EdgeType.RENDERS.value] == 2.0
    assert EDGE_WEIGHTS[EdgeType.REFERENCES.value] == 0.8

    assert NodeType.COMPONENT.value in LOGIC_NODE_TYPES
    assert NodeType.HOOK.value in LOGIC_NODE_TYPES
    assert NodeType.SERVICE.value in LOGIC_NODE_TYPES
    assert NodeType.MODULE.value in LOGIC_NODE_TYPES
    assert NodeType.ASSET.value not in LOGIC_NODE_TYPES
    assert NodeType.FOLDER.value not in LOGIC_NODE_TYPES
    assert NodeType.FILE.value not in LOGIC_NODE_TYPES
    assert NodeType.CONFIG.value not in LOGIC_NODE_TYPES

    assert CONTAINER_NODE_TYPES == {NodeType.FILE.value, NodeType.FOLDER.value}
    assert EXCLUDED_FILE_TYPES == {NodeType.FOLDER.value, NodeType.ASSET.value}
    assert NodeType.COMMUNITY.value in NON_ENTITY_NODE_TYPES
    assert WIRE_EDGE_TYPES.issubset(SEMANTIC_IMPACT_EDGES)
    assert EdgeType.IMPORTS.value in SEMANTIC_IMPACT_EDGES
    assert EdgeType.CONTAINS.value not in SEMANTIC_IMPACT_EDGES

    exported = EntityNode(
        id="src.App.App",
        name="App",
        node_type=NodeType.COMPONENT,
        is_exported=True,
    )
    assert exported.to_dict()["is_exported"] is True
