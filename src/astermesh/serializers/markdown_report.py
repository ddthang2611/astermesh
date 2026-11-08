from itertools import islice
from pathlib import Path
import networkx as nx
from astermesh.graph.model import CommunityInfo, EdgeType, NodeType


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
        ntype = data.get("type", "unknown")
        type_counts[ntype] = type_counts.get(ntype, 0) + 1

    logic_types = {NodeType.CLASS.value, NodeType.FUNCTION.value}
    logic_nodes = [n for n, d in graph.nodes(data=True) if d.get("type") in logic_types]
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
        cycles = list(islice(nx.simple_cycles(file_import_graph), 3))
    except Exception:
        pass

    lines = [
        f"# Architecture Snapshot: `{root_name}`\n",
        "## 1. Macro Statistics",
        f"- Total Elements: {total_nodes} nodes, {total_edges} relations",
        f"- Breakdown: {type_counts.get('class', 0)} Classes, {type_counts.get('function', 0)} Functions, "
        f"{type_counts.get('variable', 0)} Variables, {type_counts.get('file', 0)} Files\n",
        "## 2. Functional Communities (Leiden Clusters)",
        "| ID | Community Name | Central God Node | Size |",
        "| :--- | :--- | :--- | :--- |",
    ]

    for c in communities[:8]:
        lines.append(f"| {c.id} | `{c.name}` | `{c.god_node}` | {c.size} entities |")

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
