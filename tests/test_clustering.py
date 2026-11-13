import networkx as nx

from astermesh.graph.clustering import CommunityClusterer
from astermesh.graph.model import EdgeType, NodeType


def test_deterministic_clustering():
    # arrange: build two connected components
    g = nx.MultiDiGraph()

    # component 1
    for i in range(5):
        g.add_node(f"comp1.fn{i}", type=NodeType.FUNCTION.value, name=f"fn{i}")
    for i in range(1, 5):
        g.add_edge("comp1.fn0", f"comp1.fn{i}", type=EdgeType.CALLS.value)

    # component 2
    for i in range(5):
        g.add_node(f"comp2.fn{i}", type=NodeType.FUNCTION.value, name=f"fn{i}")
    for i in range(1, 5):
        g.add_edge("comp2.fn0", f"comp2.fn{i}", type=EdgeType.CALLS.value)

    clusterer1 = CommunityClusterer(seed=0)
    clusterer2 = CommunityClusterer(seed=0)

    # act
    comms1 = clusterer1.cluster(g)
    comms2 = clusterer2.cluster(g)

    # assert
    assert len(comms1) == len(comms2)
    names1 = [c.name for c in comms1]
    names2 = [c.name for c in comms2]
    assert names1 == names2

    # verify god node has highest degree
    for c in comms1:
        assert c.god_node in ("comp1.fn0", "comp2.fn0")
        assert c.size == 5


# kiem thu chon god node theo trong so, khu khuyen va che do cpm
def test_weighted_god_node_self_loop_exclusion_and_cpm_mode():
    clusterer_empty = CommunityClusterer()
    assert clusterer_empty.cluster(nx.MultiDiGraph()) == []

    g_non_logic = nx.MultiDiGraph()
    g_non_logic.add_node("src", type=NodeType.FOLDER.value)
    g_non_logic.add_node("src/logo.svg", type=NodeType.ASSET.value)
    g_non_logic.add_node("pyproject.toml", type=NodeType.CONFIG.value)
    assert clusterer_empty.cluster(g_non_logic) == []

    # hub: it canh nhung nang (DEFINES 3.0 + INJECTS 2.5 + CALLS 1.5 = 7.0)
    # leaf: nhieu canh hon nhung nhe (CALLS 1.5 + 5 x REFERENCES 0.8 = 5.5)
    g = nx.MultiDiGraph()
    g.add_node("hub", type=NodeType.SERVICE.value, name="HubService")
    g.add_node("leaf", type=NodeType.FUNCTION.value, name="leafFn")
    g.add_node("model", type=NodeType.CLASS.value, name="Model")
    g.add_node("repo", type=NodeType.SERVICE.value, name="Repo")
    g.add_edge("hub", "model", type=EdgeType.DEFINES.value)
    g.add_edge("repo", "hub", type=EdgeType.INJECTS.value)
    g.add_edge("leaf", "hub", type=EdgeType.CALLS.value)
    for i in range(5):
        nid = f"var_{i}"
        g.add_node(nid, type=NodeType.VARIABLE.value, name=nid)
        g.add_edge("leaf", nid, type=EdgeType.REFERENCES.value)
    assert g.degree("leaf") > g.degree("hub")

    # self-loop DEFINES 3.0 se day leaf len 8.5 neu khong bi loai
    g.add_edge("leaf", "leaf", type=EdgeType.DEFINES.value)

    comms = CommunityClusterer(seed=0, partition_mode="cpm", resolution_parameter=0.01).cluster(g)
    assert len(comms) == 1
    assert comms[0].size == 9
    assert comms[0].god_node == "hub"
    assert comms[0].name == "HubService"

    # resolution cao hon tach thanh cum nho hon
    comms_fine = CommunityClusterer(seed=0, partition_mode="cpm", resolution_parameter=0.1).cluster(g)
    assert len(comms_fine) > len(comms)
    hub_comm = next(c for c in comms_fine if "hub" in c.members)
    assert hub_comm.god_node == "hub"
