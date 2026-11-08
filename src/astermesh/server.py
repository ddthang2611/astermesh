from pathlib import Path
from typing import Any, Dict, List
import networkx as nx
from mcp.server.mcpserver import MCPServer
from astermesh.graph.builder import GraphBuilder
from astermesh.graph.clustering import CommunityClusterer
from astermesh.graph.model import CommunityInfo
from astermesh.graph.watcher import WorkspaceWatcher


# khoi tao mcp daemon server
def create_mcp_server(root_path: Path, watch: bool = True) -> MCPServer:
    mcp = MCPServer("Astermesh")
    builder = GraphBuilder(root_path)
    clusterer = CommunityClusterer()
    communities: Dict[str, CommunityInfo] = {}

    def refresh_clusters() -> None:
        with builder.lock:
            comms = clusterer.cluster(builder.graph)
            communities.clear()
            for c in comms:
                communities[c.name.lower()] = c
                communities[c.id.lower()] = c

    builder.build_full()
    refresh_clusters()

    if watch:
        watcher = WorkspaceWatcher(builder, on_updated=refresh_clusters)
        watcher.start()

    @mcp.tool()
    def get_community(name: str) -> Dict[str, Any]:
        with builder.lock:
            comm = communities.get(name.lower())
            if not comm:
                available = list({c.name for c in communities.values()})
                return {
                    "error": f"Community '{name}' not found",
                    "available_communities": available,
                }
            members_info = [
                builder.graph.nodes[m]
                for m in comm.members
                if builder.graph.has_node(m)
            ]
            return {
                "id": comm.id,
                "name": comm.name,
                "god_node": comm.god_node,
                "size": comm.size,
                "members": members_info,
            }

    @mcp.tool()
    def trace_path(source: str, target: str) -> Dict[str, Any]:
        with builder.lock:
            if not builder.graph.has_node(source) or not builder.graph.has_node(target):
                return {"error": "Source or target node not found in graph"}
            try:
                path = nx.shortest_path(
                    builder.graph.to_undirected(), source=source, target=target
                )
                return {"source": source, "target": target, "path": path}
            except nx.NetworkXNoPath:
                return {"source": source, "target": target, "path": []}

    @mcp.tool()
    def locate_symbol(name: str) -> List[Dict[str, Any]]:
        with builder.lock:
            results: List[Dict[str, Any]] = []
            q = name.lower()
            for nid, data in builder.graph.nodes(data=True):
                if q in nid.lower() or q == data.get("name", "").lower():
                    results.append(dict(data))
            return results[:25]

    @mcp.tool()
    def rebuild_graph() -> Dict[str, Any]:
        builder.build_full()
        refresh_clusters()
        return {
            "status": "rebuilt",
            "nodes": builder.graph.number_of_nodes(),
            "edges": builder.graph.number_of_edges(),
            "communities": len({c.id for c in communities.values()}),
        }

    return mcp
