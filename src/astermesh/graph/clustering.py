import igraph as ig
import leidenalg
import networkx as nx

from astermesh.graph.model import EDGE_WEIGHTS, LOGIC_NODE_TYPES, CommunityInfo, EdgeType

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

        # count distinct incoming inheritance namespaces
        inherits_ns: dict[str, set[str]] = {}
        inherits_in_count: dict[str, int] = {}
        inherits_type = EdgeType.INHERITS.value
        for u, v, data in nx_graph.edges(data=True):
            if (
                data.get("type") == inherits_type
                and u in node_to_idx
                and v in node_to_idx
                and u != v
            ):
                ns = str(
                    nx_graph.nodes[u].get("namespace_fqn")
                    or nx_graph.nodes[u].get("file_path")
                    or u
                )
                if ns:
                    inherits_ns.setdefault(v, set()).add(ns)
                inherits_in_count[v] = inherits_in_count.get(v, 0) + 1

        # symmetric weighted undirected projection without self-loops
        pair_weights: dict[tuple[int, int], float] = {}
        for u, v, data in nx_graph.edges(data=True):
            if u in node_to_idx and v in node_to_idx and u != v:
                etype = str(data.get("type", ""))
                w = EDGE_WEIGHTS.get(etype, 1.0)
                if etype == inherits_type:
                    distinct_ns = len(inherits_ns.get(v, ()))
                    in_count = inherits_in_count.get(v, 0)
                    if distinct_ns >= 8 and in_count >= 20:
                        continue
                    if distinct_ns >= 5:
                        w = w / distinct_ns
                iu = node_to_idx[u]
                iv = node_to_idx[v]
                pair = (iu, iv) if iu < iv else (iv, iu)
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

            fam_counts: dict[str, int] = {}
            for m in members:
                nx_graph.nodes[m]["community_id"] = cid
                fam = str(nx_graph.nodes[m].get("language_family") or "")
                if fam:
                    fam_counts[fam] = fam_counts.get(fam, 0) + 1

            dominant_family = (
                min(fam_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
                if fam_counts
                else ""
            )
            communities.append(
                CommunityInfo(
                    id=cid,
                    name=short_god,
                    god_node=god_node,
                    size=len(members),
                    members=members,
                    language_family=dominant_family,
                )
            )

        communities.sort(key=lambda c: c.size, reverse=True)
        return communities
