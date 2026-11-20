from collections.abc import Sequence
from pathlib import Path
from typing import Any

import networkx as nx
from mcp.server.mcpserver import MCPServer

from astermesh.graph.builder import GraphBuilder
from astermesh.graph.clustering import CommunityClusterer
from astermesh.graph.model import WIRE_EDGE_TYPES, CommunityInfo
from astermesh.graph.query import GraphQuery
from astermesh.graph.watcher import WorkspaceWatcher
from astermesh.serializers.compact_wire import render_compact_wire
from astermesh.serializers.markdown_report import render_markdown_report


# isolate single community with direct targets
def _build_community_subgraph(
    graph: nx.MultiDiGraph, comm: CommunityInfo
) -> nx.MultiDiGraph:
    sub = nx.MultiDiGraph()
    comm_set = {m for m in comm.members if graph.has_node(m)}
    keep_nodes: set[str] = set(comm_set)

    for m in comm_set:
        for _, target, edata in graph.out_edges(m, data=True):
            if str(edata.get("type", "")) in WIRE_EDGE_TYPES and graph.has_node(target):
                keep_nodes.add(target)

    for nid in keep_nodes:
        ndata = dict(graph.nodes[nid])
        sub.add_node(nid, **ndata)
        fpath = ndata.get("file_path")
        if fpath and fpath != "." and graph.has_node(fpath):
            sub.add_node(fpath, **dict(graph.nodes[fpath]))

    for m in comm_set:
        for _, target, edata in graph.out_edges(m, data=True):
            if target in keep_nodes and str(edata.get("type", "")) in WIRE_EDGE_TYPES:
                etype = str(edata.get("type", ""))
                sub.add_edge(m, target, key=etype, type=etype)

    return sub


SERVER_INSTRUCTIONS = """
Astermesh provides a deterministic in-memory code knowledge graph and context layer.
It indexes physical AST entities (Python, JS/TS, React, Vue, Svelte, Angular),
satellite configs (.toml, .json, .md), isolated assets, and functional communities.

Available MCP Resources:
- `codebase://context`: Dense #ACN2 wire representation (L0/L1/L2) of the repository graph.
- `codebase://report`: Macro architecture snapshot (communities, God nodes, dependency cycles).
- `codebase://communities`: Summary list of all detected functional modules.

Recommended workflow for code intelligence:
- Use `get_community_wire` to zoom into a single functional community in dense #ACN2 notation.
- Use `locate_symbol` to resolve file paths and physical line ranges [line_start, line_end].
- Use `get_file_outline` to inspect the structural outline of any source or config/doc file.
- Use `read_file_snippet` to read only exact target line ranges instead of reading entire files.
- Use `get_neighbors` to inspect 1-hop callers ('in') or callees/assets ('out') of an entity.
- Use `get_impact_radius` to compute transitive callers up to max_depth before modifying code.
- Use `trace_path` to find shortest dependency pathways between two entities.
- Use `get_community` to retrieve all entities in a functional subsystem.
"""


# initialize mcp daemon server
def create_mcp_server(
    root_path: Path,
    watch: bool = True,
    context_max_tokens: int | None = None,
    exclude_patterns: Sequence[str] | None = None,
    respect_gitignore: bool = False,
) -> MCPServer:
    resolved_root = root_path.resolve()
    mcp = MCPServer(name="Astermesh", instructions=SERVER_INSTRUCTIONS)
    builder = GraphBuilder(
        resolved_root,
        exclude_patterns=exclude_patterns,
        respect_gitignore=respect_gitignore,
    )
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

    @mcp.resource("codebase://context")
    def get_codebase_context() -> str:
        """Dense line-compressed #ACN2 representation of the codebase knowledge graph."""
        with builder.lock:
            return render_compact_wire(
                builder.graph,
                communities_list,
                resolved_root.name,
                max_tokens=context_max_tokens,
            )

    @mcp.resource("codebase://report")
    def get_codebase_report() -> str:
        """Macro architectural summary including communities, core hubs, and dependency cycles."""
        with builder.lock:
            return render_markdown_report(builder.graph, communities_list, resolved_root.name)

    @mcp.resource("codebase://communities")
    def get_codebase_communities() -> str:
        """List functional Leiden architectural communities and summarize size-1 singletons."""
        with builder.lock:
            lines = ["# Discovered Architectural Communities"]
            functional = [c for c in communities_list if c.size >= 2]
            singleton_count = sum(1 for c in communities_list if c.size <= 1)
            for c in functional:
                lines.append(
                    f"- Community [{c.id}] `{c.name}`: "
                    f"God Node=`{c.god_node}`, Size={c.size} entities"
                )
            if singleton_count > 0:
                lines.append(
                    f"- Isolated Singletons (size=1): {singleton_count} standalone entities"
                )
            return "\n".join(lines)

    @mcp.tool()
    def get_community(name: str) -> dict[str, Any]:
        """Retrieve all entities belonging to a functional architectural community (Leiden cluster).

        Args:
            name: Community ID (e.g. 'C1') or community name (case-insensitive).

        Returns:
            Community metadata with god_node, size, and member entity coordinates.
        """
        with builder.lock:
            comm = community_index.get(name.lower())
            if not comm:
                available = [c.name for c in communities_list]
                return {
                    "error": f"Community '{name}' not found",
                    "available_communities": available,
                }
            members_info = [
                dict(builder.graph.nodes[m])
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
    def get_community_wire(name: str, max_tokens: int | None = None) -> str:
        """Render a single functional community in dense #ACN2 wire notation for targeted zoom-in.

        Args:
            name: Community ID (e.g. 'C1') or community name (case-insensitive).
            max_tokens: Optional token budget triggering L0/L1/L2 compression on the subgraph.

        Returns:
            Standalone #ACN2 payload scoped to the selected community and its direct targets.
        """
        with builder.lock:
            comm = community_index.get(name.lower())
            if not comm:
                available = ", ".join(f"{c.id}:{c.name}" for c in communities_list[:20])
                return f"Error: Community '{name}' not found. Available: {available}"
            sub = _build_community_subgraph(builder.graph, comm)
            return render_compact_wire(
                sub,
                [comm],
                resolved_root.name,
                max_tokens=max_tokens,
            )

    @mcp.tool()
    def get_neighbors(symbol: str, direction: str = "both") -> dict[str, Any]:
        """Retrieve 1-hop incoming callers/referrers and outgoing dependencies of a symbol or asset.

        Args:
            symbol: Symbol name, Class.method suffix, FQN, or relative asset path.
            direction: Edge traversal direction: 'in', 'out', or 'both' (default).

        Returns:
            Dictionary with incoming_callers and outgoing_dependencies and line coordinates.
        """
        with builder.lock:
            return GraphQuery(builder.graph).neighbors(symbol, direction)

    @mcp.tool()
    def get_impact_radius(symbol: str, max_depth: int = 2) -> dict[str, Any]:
        """Compute transitive blast radius and affected files when modifying a symbol or asset.

        Args:
            symbol: Symbol name, Class.method suffix, FQN, or asset file path.
            max_depth: Maximum number of reverse-dependency hops to scan (default: 2).

        Returns:
            Risk assessment (LOW/MEDIUM/HIGH), affected files, and callers grouped by hop depth.
        """
        with builder.lock:
            return GraphQuery(builder.graph).impact_radius(symbol, max_depth)

    @mcp.tool()
    def get_file_outline(file_path: str) -> dict[str, Any]:
        """Extract the full structural outline (classes, functions, components, config) of a file.

        Args:
            file_path: Workspace-relative or suffix file path (e.g. 'src/server.py', 'README.md').

        Returns:
            Sorted list of all AST or satellite entities with physical [line_start, line_end].
        """
        with builder.lock:
            return GraphQuery(builder.graph).file_outline(file_path)

    @mcp.tool()
    def trace_path(source: str, target: str) -> dict[str, Any]:
        """Find the shortest dependency or interaction path between two entities in the codebase.

        Args:
            source: Starting symbol name, suffix, or node ID.
            target: Target symbol name, suffix, or node ID.

        Returns:
            Ordered list of node IDs forming the shortest path between source and target.
        """
        with builder.lock:
            return GraphQuery(builder.graph).trace_path(source, target)

    @mcp.tool()
    def locate_symbol(name: str) -> list[dict[str, Any]]:
        """Locate code symbols, UI components, hooks, config keys, or doc headings by name or FQN.

        Args:
            name: Search query matching bare symbol name, dot-path suffix, or partial FQN.

        Returns:
            Up to 25 matching entity records with file_path, type, and [line_start, line_end].
        """
        with builder.lock:
            return GraphQuery(builder.graph).locate(name)

    @mcp.tool()
    def read_file_snippet(
        file_path: str, start_line: int = 1, end_line: int | None = None
    ) -> dict[str, Any]:
        """Read a targeted physical line slice from any source or configuration file in workspace.

        Args:
            file_path: Relative file path within workspace root (e.g. 'src/astermesh/cli.py').
            start_line: 1-based starting line number (default: 1).
            end_line: 1-based inclusive ending line number (defaults to start_line + 120).

        Returns:
            Dictionary with resolved path, clamped line span, and line-numbered source snippet.
        """
        clean_rel = file_path.replace("\\", "/").lstrip("/")
        target = (resolved_root / clean_rel).resolve()
        if not target.is_relative_to(resolved_root) or not target.is_file():
            return {"error": f"File '{file_path}' not found inside workspace."}

        try:
            all_lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return {"error": f"Failed to read '{file_path}': {exc}"}

        total = len(all_lines)
        s = max(1, start_line)
        e = min(total, end_line if end_line is not None else s + 120)
        if e < s:
            e = min(total, s)
        max_window = 200
        if (e - s + 1) > max_window:
            e = s + max_window - 1

        numbered = "\n".join(f"{idx}: {all_lines[idx - 1]}" for idx in range(s, e + 1))
        return {
            "file_path": target.relative_to(resolved_root).as_posix(),
            "line_start": s,
            "line_end": e,
            "total_lines": total,
            "content": numbered,
        }

    @mcp.tool()
    def rebuild_graph() -> dict[str, Any]:
        """Trigger a full two-pass re-scan and Leiden re-clustering of the knowledge graph.

        Returns:
            Summary counts of indexed nodes, edges, and discovered architectural communities.
        """
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
