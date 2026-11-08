from pathlib import Path
import xml.etree.ElementTree as ET
from xml.dom import minidom
import networkx as nx
from astermesh.graph.model import CommunityInfo, EdgeType, NodeType


# render compact or verbose semantic xml string in memory
def render_semantic_xml(
    graph: nx.MultiDiGraph,
    communities: list[CommunityInfo],
    root_name: str,
    compact: bool = True,
) -> str:
    if not compact:
        return _render_verbose_xml(graph, communities, root_name)

    root_el = ET.Element("c_graph", root=root_name)

    # 1. build file dictionary
    file_map: dict[str, str] = {}
    file_idx = 1

    for _, data in graph.nodes(data=True):
        fpath = data.get("file_path")
        if fpath and fpath != "." and fpath not in file_map:
            fid = f"f{file_idx}"
            file_map[fpath] = fid
            file_idx += 1

    files_el = ET.SubElement(root_el, "files")
    for fpath, fid in file_map.items():
        ET.SubElement(files_el, "f", id=fid, p=fpath)

    type_abbr = {
        NodeType.CLASS.value: "c",
        NodeType.FUNCTION.value: "f",
        NodeType.VARIABLE.value: "v",
    }

    # 2. build communities and entities with inline attributes
    for comm in communities:
        comm_el = ET.SubElement(
            root_el,
            "c",
            id=str(comm.id),
            name=str(comm.name),
            size=str(comm.size),
        )
        for member_id in comm.members:
            if not graph.has_node(member_id):
                continue
            ndata = graph.nodes[member_id]
            fpath = ndata.get("file_path", "")
            fid = file_map.get(fpath, "")
            ntype = type_abbr.get(ndata.get("type"), "e")
            l_start = ndata.get("line_start") or 1
            l_end = ndata.get("line_end") or l_start

            short_id = ndata.get("name") or member_id.split(".")[-1]

            calls_list: list[str] = []
            inherits_list: list[str] = []
            refs_list: list[str] = []

            for _, target, edata in graph.out_edges(member_id, data=True):
                etype = edata.get("type")
                if not graph.has_node(target):
                    continue
                tdata = graph.nodes[target]
                t_file = tdata.get("file_path", "")
                t_fid = file_map.get(t_file, "")
                t_name = tdata.get("name") or target.split(".")[-1]

                target_ref = t_name if t_fid == fid else f"{t_fid}:{t_name}"

                if etype == EdgeType.CALLS.value:
                    calls_list.append(target_ref)
                elif etype == EdgeType.INHERITS.value:
                    inherits_list.append(target_ref)
                elif etype == EdgeType.REFERENCES.value:
                    refs_list.append(target_ref)

            ent_attrs = {
                "id": short_id,
                "t": ntype,
                "f": fid,
                "l": f"{l_start}-{l_end}",
            }
            if calls_list:
                ent_attrs["c"] = ",".join(calls_list)
            if inherits_list:
                ent_attrs["inh"] = ",".join(inherits_list)
            if refs_list:
                ent_attrs["ref"] = ",".join(refs_list)

            ET.SubElement(comm_el, "e", **ent_attrs)

    raw_bytes = ET.tostring(root_el, encoding="utf-8")
    pretty_xml = minidom.parseString(raw_bytes).toprettyxml(indent="  ")

    header_comment = (
        "<!-- Astermesh Compact Knowledge Graph\n"
        "     Legend: f=file_id, t=type (c:class, f:func, v:var), l=lines [start-end],\n"
        "             c=calls, inh=inherits, ref=references. Remote targets use 'fX:SymbolName' -->\n"
    )
    lines = pretty_xml.split("\n")
    if lines and lines[0].startswith("<?xml"):
        pretty_xml = lines[0] + "\n" + header_comment + "\n".join(lines[1:])

    return pretty_xml


def _render_verbose_xml(
    graph: nx.MultiDiGraph,
    communities: list[CommunityInfo],
    root_name: str,
) -> str:
    root_el = ET.Element("codebase_knowledge_graph", root=root_name)
    for comm in communities:
        comm_el = ET.SubElement(
            root_el,
            "community",
            id=str(comm.id),
            name=str(comm.name),
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
    return minidom.parseString(raw_bytes).toprettyxml(indent="  ")


# export semantic xml to disk
def export_semantic_xml(
    graph: nx.MultiDiGraph,
    communities: list[CommunityInfo],
    root_name: str,
    output_path: Path,
    compact: bool = True,
) -> str:
    pretty_xml = render_semantic_xml(graph, communities, root_name, compact=compact)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(pretty_xml, encoding="utf-8")
    return pretty_xml
