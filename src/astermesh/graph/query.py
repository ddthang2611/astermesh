from collections import deque
from pathlib import PurePosixPath
from typing import Any

import networkx as nx

from astermesh.graph.model import CONTAINER_NODE_TYPES, SEMANTIC_IMPACT_EDGES

_CONTAINER_TYPES = CONTAINER_NODE_TYPES
_SEMANTIC_IMPACT_EDGES = SEMANTIC_IMPACT_EDGES

_LANGUAGE_ALIASES: dict[str, str] = {
    "py": "python",
    "python": "python",
    "js": "js_ts_web",
    "ts": "js_ts_web",
    "jsx": "js_ts_web",
    "tsx": "js_ts_web",
    "javascript": "js_ts_web",
    "typescript": "js_ts_web",
    "vue": "js_ts_web",
    "svelte": "js_ts_web",
    "web": "js_ts_web",
    "js_ts_web": "js_ts_web",
    "java": "java",
    "satellite": "satellite",
}
_PYTHON_SUFFIXES = frozenset({".py", ".pyi"})
_JS_TS_WEB_SUFFIXES = frozenset({
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".mts",
    ".cts",
    ".vue",
    ".svelte",
})
_JAVA_SUFFIXES = frozenset({".java"})
_SATELLITE_SUFFIXES = frozenset({
    ".toml",
    ".json",
    ".yaml",
    ".yml",
    ".md",
    ".ini",
    ".cfg",
    ".rst",
    ".xml",
    ".properties",
    ".gradle",
    ".kts",
})
_SATELLITE_NAMES = frozenset({"dockerfile", "makefile", "containerfile"})


def _normalize_language(language: str | None) -> str | None:
    if not language:
        return None
    key = language.strip().lower()
    if not key:
        return None
    return _LANGUAGE_ALIASES.get(key, key)


def _normalize_prefix_parts(path_prefix: str | None) -> tuple[str, ...] | None:
    if not path_prefix:
        return None
    cleaned = path_prefix.replace("\\", "/").strip("/")
    if cleaned.startswith("./"):
        cleaned = cleaned[2:]
    if not cleaned or cleaned == ".":
        return None
    parts = PurePosixPath(cleaned).parts
    return parts if parts else None


def _node_language_family(data: dict[str, Any]) -> str:
    raw_fam = str(data.get("language_family") or "").strip().lower()
    if raw_fam:
        return _LANGUAGE_ALIASES.get(raw_fam, raw_fam)
    fpath = str(data.get("file_path") or "")
    if not fpath or fpath == ".":
        return ""
    p = PurePosixPath(fpath.replace("\\", "/"))
    suffix = p.suffix.lower()
    fname = p.name.lower()
    if suffix in _PYTHON_SUFFIXES:
        return "python"
    if suffix in _JS_TS_WEB_SUFFIXES:
        return "js_ts_web"
    if suffix in _JAVA_SUFFIXES:
        return "java"
    if (
        suffix in _SATELLITE_SUFFIXES
        or fname in _SATELLITE_NAMES
        or fname.startswith(("license", "requirements"))
    ):
        return "satellite"
    return ""


# centralized graph query engine
class GraphQuery:
    def __init__(self, graph: nx.MultiDiGraph) -> None:
        self.graph = graph

    def _matches_filters(
        self,
        data: dict[str, Any],
        canonical_lang: str | None,
        prefix_parts: tuple[str, ...] | None,
    ) -> bool:
        if canonical_lang is not None and _node_language_family(data) != canonical_lang:
            return False
        if prefix_parts is not None:
            raw_path = data.get("file_path")
            if not raw_path or raw_path == ".":
                return False
            cleaned = str(raw_path).replace("\\", "/").strip("/")
            if cleaned.startswith("./"):
                cleaned = cleaned[2:]
            node_parts = PurePosixPath(cleaned).parts
            if (
                len(node_parts) < len(prefix_parts)
                or node_parts[: len(prefix_parts)] != prefix_parts
            ):
                return False
        return True

    # resolve symbol to exact node id supporting dot-path suffixes
    def resolve_node_id(
        self,
        symbol: str,
        language: str | None = None,
        path_prefix: str | None = None,
    ) -> str | None:
        canonical_lang = _normalize_language(language)
        prefix_parts = _normalize_prefix_parts(path_prefix)

        if self.graph.has_node(symbol) and self._matches_filters(
            self.graph.nodes[symbol], canonical_lang, prefix_parts
        ):
            return str(symbol)

        q = symbol.lower()
        suffix_target = f".{q}"
        exact_name_match: str | None = None
        suffix_matches: list[str] = []

        for nid, data in self.graph.nodes(data=True):
            if not self._matches_filters(data, canonical_lang, prefix_parts):
                continue
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

    # locate symbols sorted by match specificity
    def locate(
        self,
        symbol: str,
        limit: int = 25,
        include_containers: bool = True,
        language: str | None = None,
        path_prefix: str | None = None,
    ) -> list[dict[str, Any]]:
        canonical_lang = _normalize_language(language)
        prefix_parts = _normalize_prefix_parts(path_prefix)
        ranked: list[tuple[tuple[int, int, int, str], dict[str, Any]]] = []
        q = symbol.lower()
        suffix_target = f".{q}"

        for nid, data in self.graph.nodes(data=True):
            if not self._matches_filters(data, canonical_lang, prefix_parts):
                continue
            nid_str = str(nid)
            nid_lower = nid_str.lower()
            name_lower = str(data.get("name") or "").lower()
            ntype = str(data.get("type") or "")
            is_container = ntype in _CONTAINER_TYPES
            if not include_containers and is_container:
                continue

            if is_container:
                if q == nid_lower or q == name_lower:
                    tier = 5
                elif q in name_lower or q in nid_lower:
                    tier = 6
                else:
                    continue
            elif q == nid_lower or q == name_lower:
                tier = 0
            elif nid_lower.endswith(suffix_target):
                tier = 1
            elif name_lower.startswith(q):
                tier = 2
            elif q in name_lower:
                tier = 3
            elif q in nid_lower:
                tier = 4
            else:
                continue

            missing_line = 0 if data.get("line_start") is not None else 1
            node_dict = dict(data)
            node_dict.setdefault("id", nid_str)
            ranked.append(((tier, missing_line, len(nid_str), nid_lower), node_dict))

        ranked.sort(key=lambda item: item[0])
        return [item[1] for item in ranked[:limit]]

    # retrieve 1-hop logic neighborhood
    def neighbors(
        self,
        symbol: str,
        direction: str = "both",
        language: str | None = None,
        path_prefix: str | None = None,
    ) -> dict[str, Any]:
        target_id = self.resolve_node_id(symbol, language=language, path_prefix=path_prefix)
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
    def impact_radius(
        self,
        symbol: str,
        max_depth: int = 2,
        language: str | None = None,
        path_prefix: str | None = None,
    ) -> dict[str, Any]:
        target_id = self.resolve_node_id(symbol, language=language, path_prefix=path_prefix)
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
                etype = str(edata.get("type") or "")
                if etype not in _SEMANTIC_IMPACT_EDGES:
                    continue
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
