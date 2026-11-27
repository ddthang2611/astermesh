from pathlib import Path
from typing import Any

import igraph as ig
import networkx as nx

from astermesh.graph.model import (
    EDGE_WEIGHTS,
    EXCLUDED_FILE_TYPES,
    LOGIC_NODE_TYPES,
    NON_ENTITY_NODE_TYPES,
    WIRE_EDGE_TYPES,
    CommunityInfo,
    EdgeType,
    NodeType,
)

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
    NodeType.ASSET.value: "a",
}

_EDGE_OP_ORDER: tuple[tuple[str, str], ...] = (
    (EdgeType.INHERITS.value, "^"),
    (EdgeType.RENDERS.value, "~"),
    (EdgeType.INJECTS.value, "+"),
    (EdgeType.CALLS.value, ">"),
    (EdgeType.REFERENCES.value, "&"),
)

_WIRE_EDGE_TYPES = WIRE_EDGE_TYPES
_EXCLUDED_FILE_TYPES = EXCLUDED_FILE_TYPES
_EXCLUDED_ENTITY_TYPES = NON_ENTITY_NODE_TYPES

_ENCODER_STATE: dict[str, Any] = {"checked": False, "encoder": None}

# saliency ranking weights for pruning
_ALPHA_PAGERANK = 1.0
_BETA_GOD_NODE = 2.0
_GAMMA_BRIDGE = 1.5


def _get_tiktoken_encoder() -> Any:
    if not _ENCODER_STATE["checked"]:
        _ENCODER_STATE["checked"] = True
        try:
            import tiktoken

            _ENCODER_STATE["encoder"] = tiktoken.get_encoding("cl100k_base")
        except (ImportError, OSError, ValueError, RuntimeError):
            _ENCODER_STATE["encoder"] = None
    return _ENCODER_STATE["encoder"]


# estimate token count matching benchmark
def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    enc = _get_tiktoken_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except (ValueError, RuntimeError):
            pass
    return max(1, len(text) // 4)


def _format_header(root_name: str, mode: str) -> str:
    return (
        f"#ACN2 root:{root_name} mode:{mode} "
        "t={c:cls,f:fn,v:var,u:ui,k:hook,s:svc,m:mod,g:cfg,d:doc,a:ast} "
        "ops={^:inh,~:ui,+:inj,>:call,&:ref}"
    )


def _format_targets(targets: list[str], prefix: str) -> str:
    if not targets:
        return ""
    return f"{prefix}{','.join(sorted(set(targets)))}"


def _format_weight(weight: float) -> str:
    if float(weight).is_integer():
        return str(int(weight))
    return f"{weight:.2f}".rstrip("0").rstrip(".")


def _collect_tables(
    graph: nx.MultiDiGraph,
    allowed_file_paths: set[str] | None = None,
    allowed_asset_ids: set[str] | None = None,
) -> tuple[dict[str, str], list[tuple[str, str]], dict[str, str], list[tuple[str, str]]]:
    file_paths = sorted(
        {
            str(fpath)
            for _, data in graph.nodes(data=True)
            if (fpath := data.get("file_path"))
            and fpath != "."
            and data.get("type") not in _EXCLUDED_FILE_TYPES
            and (allowed_file_paths is None or str(fpath) in allowed_file_paths)
        }
    )
    file_map = {fp: str(idx) for idx, fp in enumerate(file_paths, start=1)}
    file_items = [(fid, fp) for fp, fid in file_map.items()]

    asset_nodes = sorted(
        (str(data.get("file_path") or nid), str(nid))
        for nid, data in graph.nodes(data=True)
        if data.get("type") == NodeType.ASSET.value
        and (allowed_asset_ids is None or str(nid) in allowed_asset_ids)
    )
    asset_id_map: dict[str, str] = {}
    asset_items: list[tuple[str, str]] = []
    for idx, (apath, nid) in enumerate(asset_nodes, start=1):
        aid = f"a{idx}"
        asset_id_map[nid] = aid
        asset_items.append((aid, apath))

    return file_map, file_items, asset_id_map, asset_items


def _rewrite_path(path: str, sorted_prefixes: list[tuple[str, str]]) -> str:
    for did, prefix in sorted_prefixes:
        if path.startswith(f"{prefix}/"):
            return f"{did}/{path[len(prefix) + 1 :]}"
    return path


def _format_table_lines(
    dir_items: list[tuple[str, str]],
    file_items: list[tuple[str, str]],
    asset_items: list[tuple[str, str]],
) -> list[str]:
    lines: list[str] = []
    sorted_prefixes = sorted(dir_items, key=lambda item: (-len(item[1]), item[0]))
    if dir_items:
        d_entries = ";".join(f"{did}={prefix}" for did, prefix in dir_items)
        lines.append(f"@D {d_entries}")
    if file_items:
        f_entries = ";".join(
            f"{fid}:{_rewrite_path(fpath, sorted_prefixes)}" for fid, fpath in file_items
        )
        lines.append(f"@F {f_entries}")
    if asset_items:
        a_entries = ";".join(
            f"{aid}:{_rewrite_path(apath, sorted_prefixes)}" for aid, apath in asset_items
        )
        lines.append(f"@A {a_entries}")
    return lines


# compress shared directory prefixes monotonically
def _build_compressed_table_lines(
    file_items: list[tuple[str, str]],
    asset_items: list[tuple[str, str]],
) -> list[str]:
    all_paths = sorted({p for _, p in file_items} | {p for _, p in asset_items})
    if len(all_paths) < 2:
        return _format_table_lines([], file_items, asset_items)

    prefixes: set[str] = set()
    for p in all_paths:
        parts = p.split("/")
        for depth in range(1, len(parts)):
            prefix = "/".join(parts[:depth])
            if len(prefix) > 3:
                prefixes.add(prefix)

    candidates = sorted(prefixes, key=lambda d: (-len(d), d))
    best_lines = _format_table_lines([], file_items, asset_items)
    best_text = "\n".join(best_lines)
    best_chars = len(best_text)
    best_tokens = estimate_tokens(best_text)

    selected: list[tuple[str, str]] = []
    covered: set[str] = set()
    plen_offset = 1

    for prefix in candidates:
        matched = [p for p in all_paths if p not in covered and p.startswith(f"{prefix}/")]
        freq = len(matched)
        did = f"d{len(selected) + 1}"
        dir_overhead = len(did) + 1 + len(prefix) + (3 if not selected else 1)
        saved_chars = freq * (len(prefix) - len(did)) - dir_overhead
        if freq < 2 or saved_chars <= 0:
            continue

        cut = len(prefix) + plen_offset
        before_seg = ";".join(f"1:{p}" for p in matched)
        d_entry = f"@D {did}={prefix}\n" if not selected else f";{did}={prefix}"
        after_seg = d_entry + ";".join(f"1:{did}/{p[cut:]}" for p in matched)
        if estimate_tokens(after_seg) <= estimate_tokens(before_seg):
            selected.append((did, prefix))
            covered.update(matched)

    if not selected:
        return best_lines

    trial_lines = _format_table_lines(selected, file_items, asset_items)
    trial_text = "\n".join(trial_lines)
    if len(trial_text) < best_chars and estimate_tokens(trial_text) <= best_tokens:
        return trial_lines
    return best_lines


_RESERVED_ACN2_DELIMITERS: tuple[str, ...] = (",", "@", ":", ">", "^", "~", "&")


def _sanitize_acn2_label(label: str) -> str:
    for ch in _RESERVED_ACN2_DELIMITERS:
        if ch in label:
            label = label.replace(ch, "_")
    return label


# disambiguate duplicate bare names per file
def _build_short_id_map(graph: nx.MultiDiGraph) -> dict[str, str]:
    by_file: dict[str, list[tuple[str, str]]] = {}
    for nid, data in graph.nodes(data=True):
        if data.get("type") in _EXCLUDED_ENTITY_TYPES:
            continue
        fpath = str(data.get("file_path") or "")
        name = str(data.get("name") or str(nid).split(".")[-1])
        by_file.setdefault(fpath, []).append((str(nid), name))

    short_map: dict[str, str] = {}
    for nodes_in_file in by_file.values():
        name_counts: dict[str, int] = {}
        for _, name in nodes_in_file:
            name_counts[name] = name_counts.get(name, 0) + 1

        local_map: dict[str, str] = {}
        depths: dict[str, int] = {}
        for nid, name in nodes_in_file:
            if name_counts[name] == 1:
                local_map[nid] = _sanitize_acn2_label(name)
                depths[nid] = 1
            else:
                parts = nid.split(".")
                if parts[-1] != name:
                    local_map[nid] = _sanitize_acn2_label(parts[-1])
                    depths[nid] = 1
                elif len(parts) >= 2:
                    local_map[nid] = _sanitize_acn2_label(f"{parts[-2]}.{name}")
                    depths[nid] = 2
                else:
                    local_map[nid] = _sanitize_acn2_label(nid)
                    depths[nid] = 1

        # extend qualifier depth if collisions persist
        while True:
            label_counts: dict[str, int] = {}
            for label in local_map.values():
                label_counts[label] = label_counts.get(label, 0) + 1
            colliding = [nid for nid, label in local_map.items() if label_counts[label] > 1]
            if not colliding:
                break
            progressed = False
            for nid in colliding:
                parts = nid.split(".")
                next_depth = depths[nid] + 1
                if len(parts) >= next_depth:
                    depths[nid] = next_depth
                    local_map[nid] = _sanitize_acn2_label(".".join(parts[-next_depth:]))
                    progressed = True
                else:
                    full_label = _sanitize_acn2_label(nid)
                    if local_map[nid] != full_label:
                        local_map[nid] = full_label
                        progressed = True
            if not progressed:
                break

        short_map.update(local_map)

    return short_map


def _god_ref_for_community(
    comm: CommunityInfo,
    graph: nx.MultiDiGraph,
    file_map: dict[str, str],
    short_id_map: dict[str, str],
    include_fid: bool,
) -> str:
    if not comm.god_node:
        return comm.name
    short_god = short_id_map.get(comm.god_node, comm.god_node.split(".")[-1])
    if include_fid and graph.has_node(comm.god_node):
        gf = str(graph.nodes[comm.god_node].get("file_path") or "")
        gfid = file_map.get(gf, "")
        if gfid:
            return f"{gfid}:{short_god}"
    return short_god


def _format_sym_record(
    graph: nx.MultiDiGraph,
    nid: str,
    fid: str,
    is_god: bool,
    file_map: dict[str, str],
    asset_id_map: dict[str, str],
    short_id_map: dict[str, str],
    allowed_targets: set[str] | None,
) -> str:
    ndata = graph.nodes[nid]
    short_id = short_id_map.get(nid, str(ndata.get("name") or nid.split(".")[-1]))
    hub_mark = "*" if is_god else ""
    ntype = _TYPE_ABBR.get(str(ndata.get("type", "")), "e")
    l_start = int(ndata.get("line_start") or 1)
    l_end = max(l_start, int(ndata.get("line_end") or l_start))
    line_spec = str(l_start) if l_start == l_end else f"{l_start}-{l_end}"

    buckets: dict[str, list[str]] = {etype: [] for etype, _ in _EDGE_OP_ORDER}
    for _, target, edata in graph.out_edges(nid, data=True):
        if not graph.has_node(target):
            continue
        etype = str(edata.get("type", ""))
        if etype not in buckets:
            continue
        if (
            allowed_targets is not None
            and target not in allowed_targets
            and target not in asset_id_map
        ):
            continue

        tdata = graph.nodes[target]
        if tdata.get("type") == NodeType.ASSET.value and target in asset_id_map:
            target_ref = asset_id_map[target]
        else:
            t_file = str(tdata.get("file_path") or "")
            t_fid = file_map.get(t_file, "")
            t_short = short_id_map.get(target, str(tdata.get("name") or str(target).split(".")[-1]))
            target_ref = t_short if (not t_fid or t_fid == fid) else f"{t_fid}:{t_short}"
        buckets[etype].append(target_ref)

    ops = "".join(_format_targets(buckets[etype], op) for etype, op in _EDGE_OP_ORDER)
    return f"{short_id}{hub_mark}:{ntype}@{line_spec}{ops}"


def _group_member_blocks(
    graph: nx.MultiDiGraph,
    members: list[str],
    god_node: str | None,
    file_map: dict[str, str],
    asset_id_map: dict[str, str],
    short_id_map: dict[str, str],
    allowed_targets: set[str] | None,
) -> list[str]:
    grouped: dict[str, list[tuple[int, int, str, str, str]]] = {}
    for mid in members:
        if not graph.has_node(mid):
            continue
        ndata = graph.nodes[mid]
        fpath = str(ndata.get("file_path") or "")
        fid = file_map.get(fpath, "0")
        l_start = int(ndata.get("line_start") or 1)
        l_end = max(l_start, int(ndata.get("line_end") or l_start))
        short_id = short_id_map.get(mid, str(ndata.get("name") or mid.split(".")[-1]))
        rec = _format_sym_record(
            graph,
            mid,
            fid,
            is_god=(god_node is not None and mid == god_node),
            file_map=file_map,
            asset_id_map=asset_id_map,
            short_id_map=short_id_map,
            allowed_targets=allowed_targets,
        )
        grouped.setdefault(fid, []).append((l_start, l_end, short_id, mid, rec))

    ordered_fids = sorted(grouped.keys(), key=lambda f: (0, int(f)) if f.isdigit() else (1, f))
    blocks: list[str] = []
    for fid in ordered_fids:
        items = sorted(grouped[fid], key=lambda x: (x[0], x[1], x[2], x[3]))
        blocks.append(f"{fid}[{';'.join(item[4] for item in items)}]")
    return blocks


def _collect_unclustered_nodes(
    graph: nx.MultiDiGraph,
    comm_nodes: set[str],
    include_unconnected: bool,
) -> list[str]:
    selected: set[str] = set()
    candidates = [
        str(nid)
        for nid, data in graph.nodes(data=True)
        if nid not in comm_nodes and data.get("type") not in _EXCLUDED_ENTITY_TYPES
    ]

    if include_unconnected:
        return sorted(candidates)

    for nid in candidates:
        has_comm_link = any(v in comm_nodes for _, v in graph.out_edges(nid)) or any(
            u in comm_nodes for u, _ in graph.in_edges(nid)
        )
        if has_comm_link:
            selected.add(nid)

    # include downstream entity targets referenced by selected @U nodes
    queue = list(selected)
    while queue:
        curr = queue.pop()
        for _, target, edata in graph.out_edges(curr, data=True):
            if str(edata.get("type", "")) not in _WIRE_EDGE_TYPES:
                continue
            if (
                target not in comm_nodes
                and target not in selected
                and graph.has_node(target)
                and graph.nodes[target].get("type") not in _EXCLUDED_ENTITY_TYPES
            ):
                selected.add(str(target))
                queue.append(str(target))

    return sorted(selected)


def _min_max_normalize(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    lo = min(values.values())
    hi = max(values.values())
    span = hi - lo
    if span <= 0.0:
        return {k: 0.0 for k in values}
    return {k: (v - lo) / span for k, v in values.items()}


# compute weighted degree, pagerank, god-node, and bridge saliency
def _compute_saliency(
    graph: nx.MultiDiGraph,
    communities: list[CommunityInfo],
    node_comm: dict[str, str],
) -> tuple[dict[str, float], set[str]]:
    logic_nodes = sorted(
        {
            str(n)
            for n, d in graph.nodes(data=True)
            if d.get("type") in LOGIC_NODE_TYPES or n in node_comm
        }
    )
    if not logic_nodes:
        return {}, set()

    w_deg: dict[str, float] = {n: 0.0 for n in logic_nodes}
    pair_weights: dict[tuple[int, int], float] = {}
    node_idx = {n: idx for idx, n in enumerate(logic_nodes)}
    bridge_nodes: set[str] = set()

    for u, v, edata in graph.edges(data=True):
        w = EDGE_WEIGHTS.get(str(edata.get("type", "")), 1.0)
        if u in w_deg:
            w_deg[u] += w
        if v in w_deg:
            w_deg[v] += w

        uc = node_comm.get(u)
        vc = node_comm.get(v)
        if uc and vc and uc != vc:
            bridge_nodes.add(u)
            bridge_nodes.add(v)

        if u in node_idx and v in node_idx and u != v:
            pair = (node_idx[u], node_idx[v])
            pair_weights[pair] = pair_weights.get(pair, 0.0) + w

    pr_raw: dict[str, float] = {n: 0.0 for n in logic_nodes}
    if pair_weights:
        edges = list(pair_weights.keys())
        weights = [pair_weights[e] for e in edges]
        ig_g = ig.Graph(n=len(logic_nodes), edges=edges, directed=True)
        ig_g.es["weight"] = weights
        pr_vals = ig_g.pagerank(directed=True, weights="weight")
        for idx, n in enumerate(logic_nodes):
            pr_raw[n] = float(pr_vals[idx])

    norm_deg = _min_max_normalize(w_deg)
    norm_pr = _min_max_normalize(pr_raw)
    god_nodes = {c.god_node for c in communities if c.god_node}

    scores: dict[str, float] = {}
    for n in logic_nodes:
        scores[n] = (
            norm_deg.get(n, 0.0)
            + _ALPHA_PAGERANK * norm_pr.get(n, 0.0)
            + (_BETA_GOD_NODE if n in god_nodes else 0.0)
            + (_GAMMA_BRIDGE if n in bridge_nodes else 0.0)
        )
    return scores, bridge_nodes


def _render_l0(
    graph: nx.MultiDiGraph,
    communities: list[CommunityInfo],
    root_name: str,
    table_lines: list[str],
    file_map: dict[str, str],
    asset_id_map: dict[str, str],
    short_id_map: dict[str, str],
    include_unconnected: bool,
) -> str:
    lines: list[str] = [_format_header(root_name, "L0"), *table_lines]
    comm_nodes: set[str] = set()

    for comm in communities:
        valid_members = [m for m in comm.members if graph.has_node(m)]
        comm_nodes.update(valid_members)
        blocks = _group_member_blocks(
            graph,
            valid_members,
            comm.god_node,
            file_map,
            asset_id_map,
            short_id_map,
            allowed_targets=None,
        )
        if blocks:
            god_ref = _god_ref_for_community(comm, graph, file_map, short_id_map, include_fid=True)
            lines.append(f"@{comm.id} {comm.name} *{god_ref}|{'|'.join(blocks)}")

    u_nodes = _collect_unclustered_nodes(graph, comm_nodes, include_unconnected)
    if u_nodes:
        u_blocks = _group_member_blocks(
            graph,
            u_nodes,
            None,
            file_map,
            asset_id_map,
            short_id_map,
            allowed_targets=None,
        )
        if u_blocks:
            lines.append(f"@U {'|'.join(u_blocks)}")

    return "\n".join(lines) + "\n"


def _collect_l1_active_refs(
    graph: nx.MultiDiGraph,
    kept_all: set[str],
    kept_by_comm: list[tuple[CommunityInfo, list[str], int]],
) -> tuple[set[str], set[str]]:
    """Collect active file paths and assets referenced by kept L1 nodes."""
    active_files: set[str] = set()
    referenced_assets: set[str] = set()

    for comm, _, _ in kept_by_comm:
        if comm.god_node and graph.has_node(comm.god_node):
            gnode = graph.nodes[comm.god_node]
            gf = str(gnode.get("file_path") or "")
            if gf and gf != "." and gnode.get("type") not in _EXCLUDED_FILE_TYPES:
                active_files.add(gf)

    for nid in kept_all:
        if not graph.has_node(nid):
            continue
        ndata = graph.nodes[nid]
        fpath = str(ndata.get("file_path") or "")
        if fpath and fpath != "." and ndata.get("type") not in _EXCLUDED_FILE_TYPES:
            active_files.add(fpath)
        for _, target, edata in graph.out_edges(nid, data=True):
            if str(edata.get("type", "")) not in _WIRE_EDGE_TYPES or not graph.has_node(target):
                continue
            if graph.nodes[target].get("type") == NodeType.ASSET.value:
                referenced_assets.add(str(target))

    return active_files, referenced_assets


def _prepare_l1_communities(
    graph: nx.MultiDiGraph,
    communities: list[CommunityInfo],
    short_id_map: dict[str, str],
    saliency: dict[str, float],
    bridge_nodes: set[str],
) -> tuple[list[tuple[CommunityInfo, list[str], list[str], int]], int]:
    """Pre-filter and rank community members once before L1 binary search."""
    prepared: list[tuple[CommunityInfo, list[str], list[str], int]] = []
    max_remaining = 0
    for comm in communities:
        valid_members = [m for m in comm.members if graph.has_node(m)]
        if not valid_members:
            continue
        must_keep = [
            m for m in valid_members if m == comm.god_node or m in bridge_nodes
        ]
        must_set = set(must_keep)
        remaining = [m for m in valid_members if m not in must_set]
        remaining.sort(
            key=lambda m: (
                -saliency.get(m, 0.0),
                int(graph.nodes[m].get("line_start") or 1),
                short_id_map.get(m, m),
                m,
            )
        )
        max_remaining = max(max_remaining, len(remaining))
        prepared.append((comm, must_keep, remaining, len(valid_members)))
    return prepared, max_remaining


def _render_l1(
    graph: nx.MultiDiGraph,
    prepared_comms: list[tuple[CommunityInfo, list[str], list[str], int]],
    root_name: str,
    short_id_map: dict[str, str],
    k_per_comm: int,
) -> str:
    kept_by_comm: list[tuple[CommunityInfo, list[str], int]] = []
    kept_all: set[str] = set()

    for comm, must_keep, remaining, total_valid in prepared_comms:
        chosen = must_keep + remaining[:k_per_comm]
        pruned_count = max(0, total_valid - len(chosen))
        kept_by_comm.append((comm, chosen, pruned_count))
        kept_all.update(chosen)

    active_files, referenced_assets = _collect_l1_active_refs(graph, kept_all, kept_by_comm)
    file_map, file_items, asset_id_map, asset_items = _collect_tables(
        graph, allowed_file_paths=active_files, allowed_asset_ids=referenced_assets
    )
    table_lines = _build_compressed_table_lines(file_items, asset_items)
    lines: list[str] = [_format_header(root_name, "L1"), *table_lines]

    allowed_targets = kept_all | referenced_assets
    for comm, chosen, pruned_count in kept_by_comm:
        blocks = _group_member_blocks(
            graph,
            chosen,
            comm.god_node,
            file_map,
            asset_id_map,
            short_id_map,
            allowed_targets=allowed_targets,
        )
        god_ref = _god_ref_for_community(comm, graph, file_map, short_id_map, include_fid=True)
        suffix = f"|+{pruned_count}leaf" if pruned_count > 0 else ""
        if blocks:
            lines.append(f"@{comm.id} {comm.name} *{god_ref}|{'|'.join(blocks)}{suffix}")
        elif suffix:
            lines.append(f"@{comm.id} {comm.name} *{god_ref}{suffix}")

    return "\n".join(lines) + "\n"


def _community_language_family(graph: nx.MultiDiGraph, comm: CommunityInfo) -> str:
    fam = str(getattr(comm, "language_family", "") or "")
    if fam:
        return fam
    counts: dict[str, int] = {}
    for mid in comm.members:
        if not graph.has_node(mid):
            continue
        mfam = str(graph.nodes[mid].get("language_family") or "")
        if mfam:
            counts[mfam] = counts.get(mfam, 0) + 1
    if not counts:
        return ""
    return min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]


def _fit_l2_communities_with_floor(
    comm_entries: list[tuple[str, CommunityInfo, str]],
    comm_families: dict[str, str],
    functional_families: set[str],
    budget: int,
) -> tuple[list[tuple[str, CommunityInfo, str]], set[str], set[str], int, int]:
    """Reserve minority floor before size packing."""
    entry_costs: dict[str, int] = {
        cid: estimate_tokens(line + "\n") for cid, _, line in comm_entries
    }
    by_family: dict[str, list[tuple[str, CommunityInfo, str]]] = {
        fam: [] for fam in functional_families
    }
    for cid, comm, line in comm_entries:
        if comm.size >= 2:
            fam = comm_families.get(cid, "")
            if fam in by_family:
                by_family[fam].append((cid, comm, line))

    majority_fam = max(
        functional_families,
        key=lambda f: (
            sum(c.size for _, c, _ in by_family[f]),
            sum(entry_costs[cid] for cid, _, _ in by_family[f]),
            len(by_family[f]),
            f,
        ),
    )
    minority_fams = sorted(
        (f for f in functional_families if f != majority_fam),
        key=lambda f: (-sum(c.size for _, c, _ in by_family[f]), f),
    )

    floor_ids: set[str] = set()
    kept_ids: set[str] = set()
    used_tokens = 0

    for mfam in minority_fams:
        fam_entries = by_family[mfam]
        fam_total_cost = sum(entry_costs[cid] for cid, _, _ in fam_entries)
        fam_floor = min(fam_total_cost, int(budget * 0.20))
        fam_used = 0
        fam_count = 0
        for cid, _, _ in fam_entries:
            lt = entry_costs[cid]
            if fam_count > 0 and fam_used >= fam_floor:
                break
            if used_tokens + lt <= budget:
                floor_ids.add(cid)
                kept_ids.add(cid)
                used_tokens += lt
                fam_used += lt
                fam_count += 1
            else:
                break

    for cid, _, _ in comm_entries:
        if cid in kept_ids:
            continue
        lt = entry_costs[cid]
        if used_tokens + lt <= budget:
            kept_ids.add(cid)
            used_tokens += lt
        else:
            break

    kept = [(cid, comm, line) for cid, comm, line in comm_entries if cid in kept_ids]
    omitted_comms = len(comm_entries) - len(kept)
    omitted_nodes = sum(c.size for cid, c, _ in comm_entries if cid not in kept_ids)
    return kept, kept_ids, floor_ids, omitted_comms, omitted_nodes


def _fit_l2_communities(
    comm_entries: list[tuple[str, CommunityInfo, str]],
    budget: int,
) -> tuple[list[tuple[str, CommunityInfo, str]], set[str], int, int]:
    """Select top-size communities fitting token budget in O(N)."""
    kept: list[tuple[str, CommunityInfo, str]] = []
    kept_ids: set[str] = set()
    used_tokens = 0
    omitted_comms = 0
    omitted_nodes = 0

    for idx, (cid, comm, line) in enumerate(comm_entries):
        lt = estimate_tokens(line + "\n")
        if used_tokens + lt <= budget:
            kept.append((cid, comm, line))
            kept_ids.add(cid)
            used_tokens += lt
        else:
            omitted_comms = len(comm_entries) - idx
            omitted_nodes = sum(c.size for _, c, _ in comm_entries[idx:])
            break

    return kept, kept_ids, omitted_comms, omitted_nodes


def _render_l2(
    graph: nx.MultiDiGraph,
    communities: list[CommunityInfo],
    root_name: str,
    file_map: dict[str, str],
    short_id_map: dict[str, str],
    node_comm: dict[str, str],
    max_tokens: int,
) -> str:
    header = _format_header(root_name, "L2")
    ranked_comms = sorted(enumerate(communities), key=lambda item: (-item[1].size, item[0]))
    comm_entries: list[tuple[str, CommunityInfo, str]] = []
    for _, comm in ranked_comms:
        god_ref = _god_ref_for_community(comm, graph, file_map, short_id_map, include_fid=False)
        comm_entries.append((str(comm.id), comm, f"@{comm.id} {comm.name} *{god_ref}|{comm.size}n"))

    bridge_weights: dict[tuple[str, str], float] = {}
    for u, v, edata in graph.edges(data=True):
        sc = node_comm.get(u)
        dc = node_comm.get(v)
        if sc and dc and sc != dc:
            w = EDGE_WEIGHTS.get(str(edata.get("type", "")), 1.0)
            bridge_weights[(sc, dc)] = bridge_weights.get((sc, dc), 0.0) + w

    all_comm_lines = [line for _, _, line in comm_entries]
    edge_items = sorted(bridge_weights.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))
    all_edge_lines = [f"@E {sc}>{dc}:{_format_weight(w)}" for (sc, dc), w in edge_items]

    full_l2 = "\n".join([header, *all_comm_lines, *all_edge_lines]) + "\n"
    if estimate_tokens(full_l2) <= max_tokens:
        return full_l2

    header_tokens = estimate_tokens(header + "\n")
    if header_tokens >= max_tokens:
        return header + "\n"

    # fit community skeleton strictly inside budget
    skel_only = "\n".join([header, *all_comm_lines]) + "\n"
    skel_tokens = estimate_tokens(skel_only)
    if skel_tokens <= max_tokens:
        base_lines = [header, *all_comm_lines]
        kept_ids = {cid for cid, _, _ in comm_entries}
        cur_tokens = skel_tokens
    else:
        rem = max_tokens - header_tokens
        edge_reserve = min(int(rem * 0.15), 250) if bridge_weights and rem >= 120 else 0
        summary_reserve = 10
        comm_budget = max(0, rem - edge_reserve - summary_reserve)
        comm_families = {
            cid: _community_language_family(graph, comm) for cid, comm, _ in comm_entries
        }
        functional_families = {
            comm_families[cid]
            for cid, comm, _ in comm_entries
            if comm.size >= 2 and comm_families[cid]
        }
        floor_ids: set[str] = set()
        if len(functional_families) >= 2:
            kept_tuples, kept_ids, floor_ids, omitted_c, omitted_n = (
                _fit_l2_communities_with_floor(
                    comm_entries, comm_families, functional_families, comm_budget
                )
            )
            if not kept_tuples and comm_entries:
                kept_tuples, kept_ids, floor_ids, omitted_c, omitted_n = (
                    _fit_l2_communities_with_floor(
                        comm_entries,
                        comm_families,
                        functional_families,
                        max(0, rem - summary_reserve),
                    )
                )
        else:
            kept_tuples, kept_ids, omitted_c, omitted_n = _fit_l2_communities(
                comm_entries, comm_budget
            )
            if not kept_tuples and comm_entries:
                kept_tuples, kept_ids, omitted_c, omitted_n = _fit_l2_communities(
                    comm_entries, max(0, rem - summary_reserve)
                )
        while kept_tuples:
            trial_base = [header, *[ln for _, _, ln in kept_tuples]]
            if omitted_c > 0:
                trial_base.append(f"#omitted +{omitted_c}comm|{omitted_n}n")
            if estimate_tokens("\n".join(trial_base) + "\n") <= max_tokens:
                break
            drop_idx = next(
                (
                    i
                    for i in range(len(kept_tuples) - 1, -1, -1)
                    if kept_tuples[i][0] not in floor_ids
                ),
                len(kept_tuples) - 1,
            )
            _, dropped_c, _ = kept_tuples.pop(drop_idx)
            kept_ids.discard(str(dropped_c.id))
            omitted_c += 1
            omitted_n += dropped_c.size
        if not kept_tuples:
            return header + "\n"
        base_lines = [header, *[ln for _, _, ln in kept_tuples]]
        if omitted_c > 0:
            base_lines.append(f"#omitted +{omitted_c}comm|{omitted_n}n")
        cur_tokens = estimate_tokens("\n".join(base_lines) + "\n")

    # pack bridge edges in O(E)
    by_weight = sorted(
        bridge_weights.items(), key=lambda kv: (-kv[1], str(kv[0][0]), str(kv[0][1]))
    )
    kept_edges: list[str] = []
    misses = 0
    for (sc, dc), w in by_weight:
        if sc not in kept_ids or dc not in kept_ids:
            continue
        edge_line = f"@E {sc}>{dc}:{_format_weight(w)}"
        etok = estimate_tokens(edge_line + "\n")
        if cur_tokens + etok <= max_tokens:
            kept_edges.append(edge_line)
            cur_tokens += etok
            misses = 0
        else:
            misses += 1
            if misses >= 8:
                break

    while (
        kept_edges
        and estimate_tokens("\n".join([*base_lines, *kept_edges]) + "\n") > max_tokens
    ):
        kept_edges.pop()

    return "\n".join([*base_lines, *kept_edges]) + "\n"


# render ultra-compact dense line notation for llm context
def render_compact_wire(
    graph: nx.MultiDiGraph,
    communities: list[CommunityInfo],
    root_name: str,
    max_tokens: int | None = None,
    include_unconnected: bool = False,
) -> str:
    file_map, file_items, asset_id_map, asset_items = _collect_tables(graph)
    table_lines = _build_compressed_table_lines(file_items, asset_items)
    short_id_map = _build_short_id_map(graph)

    out_l0 = _render_l0(
        graph,
        communities,
        root_name,
        table_lines,
        file_map,
        asset_id_map,
        short_id_map,
        include_unconnected,
    )
    if max_tokens is None or estimate_tokens(out_l0) <= max_tokens:
        return out_l0

    node_comm: dict[str, str] = {}
    for comm in communities:
        cid = str(comm.id)
        for m in comm.members:
            if graph.has_node(m):
                node_comm[m] = cid

    saliency, bridge_nodes = _compute_saliency(graph, communities, node_comm)
    prepared_comms, max_remaining = _prepare_l1_communities(
        graph, communities, short_id_map, saliency, bridge_nodes
    )

    # verify k=0 floor before binary search
    best_l1: str | None = None
    cand0 = _render_l1(graph, prepared_comms, root_name, short_id_map, 0)
    if estimate_tokens(cand0) <= max_tokens:
        best_l1 = cand0
        lo, hi = 1, max_remaining
        while lo <= hi:
            mid = (lo + hi) // 2
            cand = _render_l1(graph, prepared_comms, root_name, short_id_map, mid)
            if estimate_tokens(cand) <= max_tokens:
                best_l1 = cand
                lo = mid + 1
            else:
                hi = mid - 1

    if best_l1 is not None:
        return best_l1

    return _render_l2(
        graph,
        communities,
        root_name,
        file_map,
        short_id_map,
        node_comm,
        max_tokens,
    )


# export compact wire notation to disk
def export_compact_wire(
    graph: nx.MultiDiGraph,
    communities: list[CommunityInfo],
    root_name: str,
    output_path: Path,
    max_tokens: int | None = None,
    include_unconnected: bool = False,
) -> str:
    content = render_compact_wire(
        graph,
        communities,
        root_name,
        max_tokens=max_tokens,
        include_unconnected=include_unconnected,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return content
