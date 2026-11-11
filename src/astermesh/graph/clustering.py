import igraph as ig
import leidenalg
import networkx as nx

from astermesh.graph.model import EDGE_WEIGHTS, LOGIC_NODE_TYPES, CommunityInfo

LOGIC_TYPES = set(LOGIC_NODE_TYPES)


# deterministic weighted leiden clustering and god node detection
class CommunityClusterer:
    def __init__(
        self,
        seed: int = 0,
        partition_mode: str = "modularity",
        resolution_parameter: float = 0.05,
    ) -> None:
        self.seed = seed
        self.partition_mode = partition_mode
        self.resolution_parameter = resolution_parameter

    def cluster(self, nx_graph: nx.MultiDiGraph) -> list[CommunityInfo]:
        logic_nodes = [
            n for n, data in nx_graph.nodes(data=True) if data.get("type") in LOGIC_TYPES
        ]
        if not logic_nodes:
            return []

        node_to_idx: dict[str, int] = {n: i for i, n in enumerate(logic_nodes)}
        # symmetric weighted undirected projection without self-loops
        pair_weights: dict[tuple[int, int], float] = {}
        for u, v, data in nx_graph.edges(data=True):
            if u in node_to_idx and v in node_to_idx and u != v:
                iu = node_to_idx[u]
                iv = node_to_idx[v]
                pair = (iu, iv) if iu < iv else (iv, iu)
                etype = data.get("type", "")
                w = EDGE_WEIGHTS.get(etype, 1.0)
                pair_weights[pair] = pair_weights.get(pair, 0.0) + w

        edges = list(pair_weights.keys())
        weights = [pair_weights[e] for e in edges]

        ig_graph = ig.Graph(n=len(logic_nodes), edges=edges, directed=False)
        ig_graph.es["weight"] = weights

        if self.partition_mode == "cpm":
            partition = leidenalg.find_partition(
                ig_graph,
                leidenalg.CPMVertexPartition,
                weights="weight",
                resolution_parameter=self.resolution_parameter,
                seed=self.seed,
            )
        else:
            partition = leidenalg.find_partition(
                ig_graph,
                leidenalg.ModularityVertexPartition,
                weights="weight",
                seed=self.seed,
            )

        communities: list[CommunityInfo] = []
        for cid_idx, cluster_indices in enumerate(partition):
            if not cluster_indices:
                continue
            cluster_set = set(cluster_indices)
            intra_weights: dict[int, float] = {idx: 0.0 for idx in cluster_indices}
            for (iu, iv), w in pair_weights.items():
                if iu in cluster_set and iv in cluster_set:
                    intra_weights[iu] += w
                    intra_weights[iv] += w

            god_idx = max(cluster_indices, key=lambda idx: intra_weights.get(idx, 0.0))
            god_node = logic_nodes[god_idx]
            members = [logic_nodes[i] for i in cluster_indices]
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
