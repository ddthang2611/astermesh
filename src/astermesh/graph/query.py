from collections import deque
from pathlib import PurePosixPath
from typing import Any

import networkx as nx


# centralized graph query engine
class GraphQuery:
    def __init__(self, graph: nx.MultiDiGraph) -> None:
        self.graph = graph

    # resolve symbol to exact node id supporting dot-path suffixes
    def resolve_node_id(self, symbol: str) -> str | None:
        if self.graph.has_node(symbol):
            return str(symbol)

        q = symbol.lower()
        suffix_target = f".{q}"
        exact_name_match: str | None = None
        suffix_matches: list[str] = []

        for nid, data in self.graph.nodes(data=True):
            nid_str = str(nid)
            nid_lower = nid_str.lower()
            if q == nid_lower:
                return nid_str
            if nid_lower.endswith(suffix_target):
                suffix_matches.append(nid_str)
            if exact_name_match is None and q == str(data.get("name") or "").lower():
                exact_name_match = nid_str

        if suffix_matches:
            return min(suffix_matches, key=len)
        return exact_name_match

    # format node dictionary with coordinates
    def format_node(self, nid: str) -> dict[str, Any]:
        data = self.graph.nodes[nid]
        return {
            "id": nid,
            "name": data.get("name", nid),
            "type": data.get("type"),
            "file_path": data.get("file_path"),
            "line_start": data.get("line_start"),
            "line_end": data.get("line_end"),
            "parameters": data.get("parameters", []),
            "is_async": data.get("is_async", False),
            "is_method": data.get("is_method", False),
            "is_exported": data.get("is_exported", False),
            "community_id": data.get("community_id"),
        }

    # locate symbols by name, suffix dot-path, or fqn query
    def locate(self, symbol: str, limit: int = 25) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        q = symbol.lower()
        suffix_target = f".{q}"
        for nid, data in self.graph.nodes(data=True):
            nid_str = str(nid)
            nid_lower = nid_str.lower()
            name_lower = str(data.get("name") or "").lower()
            if q in nid_lower or nid_lower.endswith(suffix_target) or q == name_lower:
                node_dict = dict(data)
                node_dict.setdefault("id", nid_str)
                results.append(node_dict)
                if len(results) >= limit:
                    break
        return results

    # retrieve 1-hop logic neighborhood
    def neighbors(self, symbol: str, direction: str = "both") -> dict[str, Any]:
        target_id = self.resolve_node_id(symbol)
        if not target_id:
            return {"error": f"Symbol '{symbol}' not found in graph."}

        incoming: list[dict[str, Any]] = []
        outgoing: list[dict[str, Any]] = []

        if direction in ("in", "both"):
            for u, _, edata in self.graph.in_edges(target_id, data=True):
                ndata = self.graph.nodes[u]
                incoming.append({
                    "id": u,
                    "name": ndata.get("name", u),
                    "type": ndata.get("type"),
                    "edge": edata.get("type"),
                    "file": ndata.get("file_path"),
                    "lines": f"{ndata.get('line_start')}-{ndata.get('line_end')}",
                })

        if direction in ("out", "both"):
            for _, v, edata in self.graph.out_edges(target_id, data=True):
                ndata = self.graph.nodes[v]
                outgoing.append({
                    "id": v,
                    "name": ndata.get("name", v),
                    "type": ndata.get("type"),
                    "edge": edata.get("type"),
                    "file": ndata.get("file_path"),
                    "lines": f"{ndata.get('line_start')}-{ndata.get('line_end')}",
                })

        return {
            "symbol": target_id,
            "incoming_callers": incoming,
            "outgoing_dependencies": outgoing,
        }

    # compute blast radius with deque bfs
    def impact_radius(self, symbol: str, max_depth: int = 2) -> dict[str, Any]:
        target_id = self.resolve_node_id(symbol)
        if not target_id:
            return {"error": f"Symbol '{symbol}' not found in graph."}

        visited = {target_id}
        queue: deque[tuple[str, int]] = deque([(target_id, 0)])
        impact_levels: dict[int, list[dict[str, Any]]] = {}
        affected_files: set[str] = set()

        while queue:
            curr_node, depth = queue.popleft()
            if depth >= max_depth:
                continue

            for u, _, edata in self.graph.in_edges(curr_node, data=True):
                if u not in visited:
                    visited.add(u)
                    ndata = self.graph.nodes[u]
                    fpath = ndata.get("file_path", "")
                    if fpath:
                        affected_files.add(fpath)
                    step_info = {
                        "id": u,
                        "name": ndata.get("name", u),
                        "type": ndata.get("type"),
                        "edge": edata.get("type"),
                        "file": fpath,
                        "lines": f"{ndata.get('line_start')}-{ndata.get('line_end')}",
                    }
                    queue.append((u, depth + 1))
                    impact_levels.setdefault(depth + 1, []).append(step_info)

        total_callers = len(visited) - 1
        risk_level = (
            "HIGH" if total_callers >= 10 else ("MEDIUM" if total_callers >= 3 else "LOW")
        )
        return {
            "target_symbol": target_id,
            "max_depth_scanned": max_depth,
            "risk_level": risk_level,
            "total_affected_entities": total_callers,
            "affected_files_count": len(affected_files),
            "affected_files": sorted(affected_files),
            "impact_by_depth": impact_levels,
        }

    # extract structural outline for a specific file path
    def file_outline(self, file_path: str) -> dict[str, Any]:
        target_parts = PurePosixPath(file_path.replace("\\", "/").strip("/")).parts
        matched_nodes: list[dict[str, Any]] = []

        for nid, data in self.graph.nodes(data=True):
            node_file = data.get("file_path")
            if not node_file or node_file == ".":
                continue
            node_parts = PurePosixPath(str(node_file).replace("\\", "/").strip("/")).parts
            if node_parts == target_parts or (
                len(node_parts) >= len(target_parts)
                and node_parts[-len(target_parts) :] == target_parts
            ):
                node_dict = dict(data)
                node_dict.setdefault("id", str(nid))
                matched_nodes.append(node_dict)

        if not matched_nodes:
            return {"error": f"File '{file_path}' not found in graph index."}

        matched_nodes.sort(key=lambda x: x.get("line_start") or 0)
        return {
            "file_path": file_path,
            "total_entities": len(matched_nodes),
            "outline": matched_nodes,
        }

    # shortest interaction path via graph view
    def trace_path(self, source: str, target: str) -> dict[str, Any]:
        src_id = self.resolve_node_id(source)
        tgt_id = self.resolve_node_id(target)
        if not src_id or not tgt_id:
            return {"error": "Source or target node not found in graph"}

        try:
            undirected_view = self.graph.to_undirected(as_view=True)
            path = nx.shortest_path(undirected_view, source=src_id, target=tgt_id)
            return {"source": src_id, "target": tgt_id, "path": path}
        except nx.NetworkXNoPath:
            return {"source": src_id, "target": tgt_id, "path": []}
