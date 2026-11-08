from pathlib import Path
from typing import Any
from mcp.server.mcpserver import MCPServer
from astermesh.graph.builder import GraphBuilder
from astermesh.graph.clustering import CommunityClusterer
from astermesh.graph.model import CommunityInfo
from astermesh.graph.query import GraphQuery
from astermesh.graph.watcher import WorkspaceWatcher
from astermesh.serializers.markdown_report import render_markdown_report

SERVER_INSTRUCTIONS = """
Astermesh provides a deterministic in-memory code knowledge graph and context layer.
It indexes physical AST entities, call relationships, inheritance hierarchies, and functional communities (via Leiden clustering).

Available MCP Resources:
- `codebase://report`: Macro architecture snapshot (statistics, Leiden communities, God nodes, and dependency cycles).
- `codebase://communities`: Summary list of all detected functional modules.

Recommended workflow for code intelligence:
- Use `locate_symbol` to quickly resolve file paths and exact physical line ranges [line_start, line_end] for functions, classes, or variables instead of broad text searches.
- Use `get_file_outline` to inspect the full structural outline of a specific file without guessing symbol names.
- Use `get_neighbors` to inspect 1-hop callers ('in') or callees ('out') of a specific entity without reading whole files.
- Use `get_impact_radius` to compute transitive callers up to max_depth before modifying core entities (blast radius analysis).
- Use `trace_path` to inspect shortest execution/dependency pathways between two logic entities across the codebase.
- Use `get_community` to retrieve all entities belonging to a specific architectural subsystem or module.
- The graph updates incrementally in real-time via file watcher. Use `rebuild_graph` only when performing manual full-rescan.
"""


# initialize mcp daemon server
def create_mcp_server(root_path: Path, watch: bool = True) -> MCPServer:
    mcp = MCPServer(name="Astermesh", instructions=SERVER_INSTRUCTIONS)
    builder = GraphBuilder(root_path)
    clusterer = CommunityClusterer()
    communities_list: list[CommunityInfo] = []
    community_index: dict[str, CommunityInfo] = {}

    def refresh_clusters() -> None:
        with builder.lock:
            comms = clusterer.cluster(builder.graph)
            communities_list.clear()
            communities_list.extend(comms)
            community_index.clear()
            for c in comms:
                community_index[c.name.lower()] = c
                community_index[c.id.lower()] = c

    builder.build_full()
    refresh_clusters()

    if watch:
        watcher = WorkspaceWatcher(builder, on_updated=refresh_clusters)
        watcher.start()

    # mcp resource: codebase architecture report
    @mcp.resource("codebase://report")
    def get_codebase_report() -> str:
        with builder.lock:
            return render_markdown_report(builder.graph, communities_list, root_path.name)

    # mcp resource: codebase communities list
    @mcp.resource("codebase://communities")
    def get_codebase_communities() -> str:
        with builder.lock:
            lines = ["# Discovered Architectural Communities"]
            for c in communities_list:
                lines.append(
                    f"- Community [{c.id}] `{c.name}`: God Node=`{c.god_node}`, Size={c.size} entities"
                )
            return "\n".join(lines)

    @mcp.tool()
    def get_community(name: str) -> dict[str, Any]:
        with builder.lock:
            comm = community_index.get(name.lower())
            if not comm:
                available = [c.name for c in communities_list]
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
    def get_neighbors(symbol: str, direction: str = "both") -> dict[str, Any]:
        with builder.lock:
            return GraphQuery(builder.graph).neighbors(symbol, direction)

    @mcp.tool()
    def get_impact_radius(symbol: str, max_depth: int = 2) -> dict[str, Any]:
        with builder.lock:
            return GraphQuery(builder.graph).impact_radius(symbol, max_depth)

    @mcp.tool()
    def get_file_outline(file_path: str) -> dict[str, Any]:
        with builder.lock:
            return GraphQuery(builder.graph).file_outline(file_path)

    @mcp.tool()
    def trace_path(source: str, target: str) -> dict[str, Any]:
        with builder.lock:
            return GraphQuery(builder.graph).trace_path(source, target)

    @mcp.tool()
    def locate_symbol(name: str) -> list[dict[str, Any]]:
        with builder.lock:
            return GraphQuery(builder.graph).locate(name)

    @mcp.tool()
    def rebuild_graph() -> dict[str, Any]:
        with builder.lock:
            builder.build_full()
            refresh_clusters()
            return {
                "status": "rebuilt",
                "nodes": builder.graph.number_of_nodes(),
                "edges": builder.graph.number_of_edges(),
                "communities": len(communities_list),
            }

    return mcp
