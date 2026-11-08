from pathlib import Path
import networkx as nx
from pyvis.network import Network


# xuat do thi tuong tac 2d cho nguoi dung
def export_html_visual(graph: nx.MultiDiGraph, output_path: Path) -> None:
    net = Network(
        height="900px",
        width="100%",
        directed=True,
        bgcolor="#1a1a1a",
        font_color="white",
    )
    color_map = {
        "folder": "#f59e0b",
        "file": "#3b82f6",
        "class": "#10b981",
        "function": "#8b5cf6",
        "variable": "#6b7280",
    }

    for node_id, data in graph.nodes(data=True):
        ntype = data.get("type", "variable")
        label = data.get("name", node_id)
        lines = (
            f" [{data.get('line_start')}-{data.get('line_end')}]"
            if data.get("line_start")
            else ""
        )
        title = f"{node_id} ({ntype}){lines}"
        net.add_node(
            node_id,
            label=label,
            title=title,
            color=color_map.get(ntype, "#9ca3af"),
        )

    for u, v, data in graph.edges(data=True):
        net.add_edge(u, v, label=data.get("type", ""))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    net.write_html(str(output_path))
