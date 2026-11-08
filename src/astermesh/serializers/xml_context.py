from pathlib import Path
from typing import List
import xml.etree.ElementTree as ET
from xml.dom import minidom
import networkx as nx
from astermesh.graph.model import CommunityInfo, EdgeType


# xuat semantic xml cho ai context
def export_semantic_xml(
    graph: nx.MultiDiGraph,
    communities: List[CommunityInfo],
    root_name: str,
    output_path: Path,
) -> str:
    root_el = ET.Element("codebase_knowledge_graph", root=root_name)

    for comm in communities:
        comm_el = ET.SubElement(
            root_el,
            "community",
            id=comm.id,
            name=comm.name,
            size=str(comm.size),
        )
        for member_id in comm.members:
            if not graph.has_node(member_id):
                continue
            ndata = graph.nodes[member_id]
            l_start = ndata.get("line_start") or 1
            l_end = ndata.get("line_end") or l_start
            ent_el = ET.SubElement(
                comm_el,
                "entity",
                id=member_id,
                type=ndata.get("type", "unknown"),
                file=ndata.get("file_path", ""),
                lines=f"{l_start}-{l_end}",
            )
            for _, target, edata in graph.out_edges(member_id, data=True):
                etype = edata.get("type")
                if etype == EdgeType.CALLS.value:
                    ET.SubElement(ent_el, "calls", target=target)
                elif etype == EdgeType.INHERITS.value:
                    ET.SubElement(ent_el, "inherits", target=target)
                elif etype == EdgeType.REFERENCES.value:
                    ET.SubElement(ent_el, "references", target=target)

    raw_bytes = ET.tostring(root_el, encoding="utf-8")
    pretty_xml = minidom.parseString(raw_bytes).toprettyxml(indent="  ")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(pretty_xml, encoding="utf-8")
    return pretty_xml
