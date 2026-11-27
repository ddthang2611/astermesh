from collections import deque
from pathlib import Path

import networkx as nx

from astermesh.graph.model import CommunityInfo, EdgeType, NodeType


# bounded bfs for shortest scc cycle
def _shortest_scc_cycle(
    graph: nx.DiGraph,
    scc: set[str],
    max_depth: int = 8,
) -> list[str] | None:
    start = min(scc)
    parent: dict[str, str] = {}
    visited: set[str] = {start}
    queue: deque[tuple[str, int]] = deque([(start, 0)])

    while queue:
        curr, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for nbr in sorted(graph.successors(curr)):
            if nbr not in scc:
                continue
            if nbr == start:
                cycle = [curr]
                node = curr
                while node != start:
                    node = parent[node]
                    cycle.append(node)
                cycle.reverse()
                return cycle
            if nbr not in visited:
                visited.add(nbr)
                parent[nbr] = curr
                queue.append((nbr, depth + 1))
    return None


# pure in-memory markdown report renderer
def render_markdown_report(
    graph: nx.MultiDiGraph,
    communities: list[CommunityInfo],
    root_name: str,
) -> str:
    total_nodes = graph.number_of_nodes()
    total_edges = graph.number_of_edges()

    type_counts: dict[str, int] = {}
    for _, data in graph.nodes(data=True):
        ntype = str(data.get("type", "unknown"))
        type_counts[ntype] = type_counts.get(ntype, 0) + 1

    hub_types = {
        NodeType.CLASS.value,
        NodeType.FUNCTION.value,
        NodeType.COMPONENT.value,
        NodeType.HOOK.value,
        NodeType.SERVICE.value,
        NodeType.MODULE.value,
    }
    logic_nodes = [n for n, d in graph.nodes(data=True) if d.get("type") in hub_types]
    sub_g = graph.subgraph(logic_nodes)
    degrees = dict(sub_g.degree())
    sorted_hubs = sorted(degrees.items(), key=lambda x: x[1], reverse=True)[:5]

    file_nodes = {n for n, d in graph.nodes(data=True) if d.get("type") == NodeType.FILE.value}
    file_import_graph = nx.DiGraph()
    for u, v, data in graph.edges(data=True):
        if data.get("type") == EdgeType.IMPORTS.value and u in file_nodes:
            target_file = graph.nodes[v].get("file_path", v)
            if target_file != u:
                file_import_graph.add_edge(u, target_file)

    cycles: list[list[str]] = []
    try:
        sccs = [
            scc
            for scc in nx.strongly_connected_components(file_import_graph)
            if len(scc) > 1
        ]
        sccs.sort(key=lambda s: (-len(s), min(s)))
        for scc in sccs[:3]:
            cyc = _shortest_scc_cycle(file_import_graph, scc, max_depth=8)
            if cyc:
                cycles.append(cyc)
    except (nx.NetworkXError, ValueError):
        cycles = []

    breakdown_line = (
        f"- Breakdown: {type_counts.get('class', 0)} Classes, "
        f"{type_counts.get('function', 0)} Functions, "
        f"{type_counts.get('component', 0)} Components, "
        f"{type_counts.get('service', 0)} Services, "
        f"{type_counts.get('hook', 0)} Hooks, "
        f"{type_counts.get('asset', 0)} Assets, "
        f"{type_counts.get('file', 0)} Files\n"
    )

    lines = [
        f"# Architecture Snapshot: `{root_name}`\n",
        "## 1. Macro Statistics",
        f"- Total Elements: {total_nodes} nodes, {total_edges} relations",
        breakdown_line,
        "## 2. Functional Communities (Leiden Clusters)",
        "| ID | Community Name | Central God Node | Size |",
        "| :--- | :--- | :--- | :--- |",
    ]

    functional = [c for c in communities if c.size >= 2]
    singleton_count = sum(1 for c in communities if c.size <= 1)

    for c in functional[:8]:
        lines.append(f"| {c.id} | `{c.name}` | `{c.god_node}` | {c.size} entities |")

    if singleton_count > 0:
        lines.append(f"\n- Isolated Singletons (size=1): {singleton_count} standalone entities")

    lines.append("\n## 3. Top Core Hubs")
    for nid, deg in sorted_hubs:
        ntype = graph.nodes[nid].get("type", "")
        fpath = graph.nodes[nid].get("file_path", "")
        lines.append(f"- `{nid}` ({ntype}, degree={deg}) -> `{fpath}`")

    if cycles:
        lines.append("\n## 4. Circular Dependency Warnings")
        for cyc in cycles:
            lines.append(f"- Cycle: {' -> '.join(cyc)} -> {cyc[0]}")

    return "\n".join(lines) + "\n"


# export summary report to disk
def export_markdown_report(
    graph: nx.MultiDiGraph,
    communities: list[CommunityInfo],
    root_name: str,
    output_path: Path,
) -> str:
    report_text = render_markdown_report(graph, communities, root_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_text, encoding="utf-8")
    return report_text
