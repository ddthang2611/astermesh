from typing import Dict, List
import igraph as ig
import leidenalg
import networkx as nx
from astermesh.graph.model import CommunityInfo, NodeType

LOGIC_TYPES = {NodeType.CLASS.value, NodeType.FUNCTION.value, NodeType.VARIABLE.value}


# phan cum leiden va dinh vi god nodes
class CommunityClusterer:
    def cluster(self, nx_graph: nx.MultiDiGraph) -> List[CommunityInfo]:
        logic_nodes = [
            n for n, data in nx_graph.nodes(data=True) if data.get("type") in LOGIC_TYPES
        ]
        if not logic_nodes:
            return []

        node_to_idx: Dict[str, int] = {n: i for i, n in enumerate(logic_nodes)}
        edges = []
        for u, v, _ in nx_graph.edges(keys=True):
            if u in node_to_idx and v in node_to_idx and u != v:
                edges.append((node_to_idx[u], node_to_idx[v]))

        ig_graph = ig.Graph(n=len(logic_nodes), edges=edges, directed=True)
        partition = leidenalg.find_partition(
            ig_graph, leidenalg.ModularityVertexPartition
        )

        sub_g = nx_graph.subgraph(logic_nodes)
        centrality = nx.degree_centrality(sub_g)
        communities: List[CommunityInfo] = []

        for cid_idx, cluster_indices in enumerate(partition):
            if not cluster_indices:
                continue
            members = [logic_nodes[i] for i in cluster_indices]
            god_node = max(members, key=lambda m: centrality.get(m, 0.0))
            short_god = nx_graph.nodes[god_node].get("name", god_node)
            cid = f"C{cid_idx + 1}"

            for m in members:
                nx_graph.nodes[m]["community_id"] = cid

            communities.append(
                CommunityInfo(
                    id=cid,
                    name=short_god,
                    god_node=god_node,
                    size=len(members),
                    members=members,
                )
            )

        communities.sort(key=lambda c: c.size, reverse=True)
        return communities
