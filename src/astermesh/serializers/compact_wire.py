from pathlib import Path

import networkx as nx

from astermesh.graph.model import CommunityInfo, EdgeType, NodeType

_TYPE_ABBR: dict[str, str] = {
    NodeType.CLASS.value: "c",
    NodeType.FUNCTION.value: "f",
    NodeType.VARIABLE.value: "v",
    NodeType.COMPONENT.value: "u",
    NodeType.HOOK.value: "k",
    NodeType.SERVICE.value: "s",
    NodeType.MODULE.value: "m",
    NodeType.CONFIG.value: "g",
    NodeType.DOC.value: "d",
}

_EXCLUDED_FILE_TYPES: frozenset[str] = frozenset({
    NodeType.FOLDER.value,
    NodeType.ASSET.value,
})


def _format_targets(targets: list[str], prefix: str) -> str:
    if not targets:
        return ""
    seen: list[str] = []
    for t in targets:
        if t not in seen:
            seen.append(t)
    return f"{prefix}{','.join(seen)}"


# render ultra-compact dense line notation for llm context
def render_compact_wire(
    graph: nx.MultiDiGraph,
    communities: list[CommunityInfo],
    root_name: str,
) -> str:
    file_map: dict[str, str] = {}
    file_idx = 1

    for _, data in graph.nodes(data=True):
        ntype = data.get("type")
        fpath = data.get("file_path")
        if (
            fpath
            and fpath != "."
            and ntype not in _EXCLUDED_FILE_TYPES
            and fpath not in file_map
        ):
            file_map[str(fpath)] = str(file_idx)
            file_idx += 1

    lines: list[str] = [
        (
            f"#ACN1 root:{root_name} "
            "t={c:cls,f:fn,v:var,u:ui,k:hook,s:svc,m:mod,g:cfg,d:doc} "
            "ops={^:inh,~:ui,+:inj,>:call,&:ref} fmt=@C<id> <god>|<fid>[<sym>:<t>@<l1>-<l2><ops>]"
        )
    ]

    if file_map:
        file_entries = ";".join(f"{fid}:{fpath}" for fpath, fid in file_map.items())
        lines.append(f"@F {file_entries}")

    for comm in communities:
        grouped_by_fid: dict[str, list[str]] = {}
        for member_id in comm.members:
            if not graph.has_node(member_id):
                continue
            ndata = graph.nodes[member_id]
            fpath = str(ndata.get("file_path", ""))
            fid = file_map.get(fpath, "0")
            ntype = _TYPE_ABBR.get(str(ndata.get("type", "")), "e")
            l_start = ndata.get("line_start") or 1
            l_end = ndata.get("line_end") or l_start
            short_id = str(ndata.get("name") or member_id.split(".")[-1])

            inh_list: list[str] = []
            ui_list: list[str] = []
            inj_list: list[str] = []
            call_list: list[str] = []
            ref_list: list[str] = []

            for _, target, edata in graph.out_edges(member_id, data=True):
                if not graph.has_node(target):
                    continue
                etype = edata.get("type")
                tdata = graph.nodes[target]
                t_file = str(tdata.get("file_path", ""))
                t_fid = file_map.get(t_file, "")
                t_name = str(tdata.get("name") or str(target).split(".")[-1])
                target_ref = t_name if (not t_fid or t_fid == fid) else f"{t_fid}:{t_name}"

                if etype == EdgeType.INHERITS.value:
                    inh_list.append(target_ref)
                elif etype == EdgeType.RENDERS.value:
                    ui_list.append(target_ref)
                elif etype == EdgeType.INJECTS.value:
                    inj_list.append(target_ref)
                elif etype == EdgeType.CALLS.value:
                    call_list.append(target_ref)
                elif etype == EdgeType.REFERENCES.value:
                    ref_list.append(target_ref)

            ops = (
                _format_targets(inh_list, "^")
                + _format_targets(ui_list, "~")
                + _format_targets(inj_list, "+")
                + _format_targets(call_list, ">")
                + _format_targets(ref_list, "&")
            )
            entry = f"{short_id}:{ntype}@{l_start}-{l_end}{ops}"
            grouped_by_fid.setdefault(fid, []).append(entry)

        if grouped_by_fid:
            blocks = "|".join(
                f"{fid}[{';'.join(entries)}]" for fid, entries in grouped_by_fid.items()
            )
            lines.append(f"@{comm.id} {comm.name}|{blocks}")

    return "\n".join(lines) + "\n"


# export compact wire notation to disk
def export_compact_wire(
    graph: nx.MultiDiGraph,
    communities: list[CommunityInfo],
    root_name: str,
    output_path: Path,
) -> str:
    content = render_compact_wire(graph, communities, root_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return content
