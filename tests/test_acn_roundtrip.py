import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx

from astermesh.graph.builder import GraphBuilder
from astermesh.graph.clustering import CommunityClusterer
from astermesh.graph.model import CommunityInfo, EdgeType, NodeType
from astermesh.serializers.compact_wire import estimate_tokens, render_compact_wire


@dataclass
class DecodedSymbol:
    fid: str
    short_id: str
    is_hub: bool
    type_code: str
    line_start: int
    line_end: int
    edges: set[tuple[str, str]] = field(default_factory=set)


@dataclass
class DecodedCommunity:
    cid: str
    name: str
    god_ref: str
    pruned_leaves: int = 0
    size_hint: int | None = None
    symbols: list[DecodedSymbol] = field(default_factory=list)


@dataclass
class DecodedACN:
    mode: str
    root: str
    dirs: dict[str, str]
    files: dict[str, str]
    assets: dict[str, str]
    communities: dict[str, DecodedCommunity]
    unclustered: list[DecodedSymbol]
    bridge_edges: dict[tuple[str, str], str]


_SYM_HEAD_RE = re.compile(r"^(.+?)(\*?):([a-z])@(\d+)(?:-(\d+))?(.*)$")
_OP_SPLIT_RE = re.compile(r"([\^~+>&])")


def _expand_dir(ref: str, dirs: dict[str, str]) -> str:
    if "/" in ref:
        head, tail = ref.split("/", 1)
        if head in dirs:
            return f"{dirs[head]}/{tail}"
    return ref


def _parse_block_list(
    blocks: list[str],
    files: dict[str, str],
    assets: dict[str, str],
) -> list[DecodedSymbol]:
    out: list[DecodedSymbol] = []
    for blk in blocks:
        m = re.match(r"^([^[]+)\[(.*)\]$", blk)
        if not m:
            continue
        fid, body = m.group(1), m.group(2)
        for raw_rec in body.split(";"):
            if not raw_rec:
                continue
            sm = _SYM_HEAD_RE.match(raw_rec)
            assert sm is not None, f"Invalid symbol record: {raw_rec}"
            short_id, hub_flag, tcode, ls_s, le_s, ops_str = sm.groups()
            ls = int(ls_s)
            le = int(le_s) if le_s else ls
            edges: set[tuple[str, str]] = set()
            if ops_str:
                parts = _OP_SPLIT_RE.split(ops_str)
                for idx in range(1, len(parts), 2):
                    op = parts[idx]
                    targets = parts[idx + 1]
                    for tgt in targets.split(","):
                        if not tgt:
                            continue
                        if tgt in assets:
                            resolved = f"asset:{assets[tgt]}"
                        elif ":" in tgt:
                            tfid, tname = tgt.split(":", 1)
                            resolved = f"{files[tfid]}::{tname}"
                        else:
                            resolved = f"{files[fid]}::{tgt}"
                        edges.add((op, resolved))
            out.append(
                DecodedSymbol(
                    fid=fid,
                    short_id=short_id,
                    is_hub=(hub_flag == "*"),
                    type_code=tcode,
                    line_start=ls,
                    line_end=le,
                    edges=edges,
                )
            )
    return out


# deterministic acn2 wire decoder
def _decode_acn2(text: str) -> DecodedACN:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    header = lines[0]
    assert header.startswith("#ACN2 ")
    root = ""
    mode = ""
    for tok in header.split():
        if tok.startswith("root:"):
            root = tok.split(":", 1)[1]
        elif tok.startswith("mode:"):
            mode = tok.split(":", 1)[1]

    dirs: dict[str, str] = {}
    files: dict[str, str] = {}
    assets: dict[str, str] = {}
    communities: dict[str, DecodedCommunity] = {}
    unclustered: list[DecodedSymbol] = []
    bridge_edges: dict[tuple[str, str], str] = {}

    for line in lines[1:]:
        assert not line[0].isspace(), f"Unexpected indentation in line: {line}"
        if line.startswith("@D "):
            for entry in line[3:].split(";"):
                did, prefix = entry.split("=", 1)
                dirs[did] = prefix
        elif line.startswith("@F "):
            for entry in line[3:].split(";"):
                fid, ref = entry.split(":", 1)
                files[fid] = _expand_dir(ref, dirs)
        elif line.startswith("@A "):
            for entry in line[3:].split(";"):
                aid, ref = entry.split(":", 1)
                assets[aid] = _expand_dir(ref, dirs)
        elif line.startswith("@U "):
            unclustered.extend(_parse_block_list(line[3:].split("|"), files, assets))
        elif line.startswith("@E "):
            edge_part, weight = line[3:].split(":", 1)
            src_c, dst_c = edge_part.split(">", 1)
            bridge_edges[(src_c, dst_c)] = weight
        elif line.startswith("@"):
            head_part, _, rest = line.partition("|")
            toks = head_part.split(" ")
            cid = toks[0][1:]
            god_ref = next((t[1:] for t in toks[1:] if t.startswith("*")), "")
            name = " ".join(t for t in toks[1:] if not t.startswith("*"))
            comm = DecodedCommunity(cid=cid, name=name, god_ref=god_ref)
            if rest:
                segs = rest.split("|")
                if mode == "L2" and len(segs) == 1 and segs[0].endswith("n"):
                    comm.size_hint = int(segs[0][:-1])
                else:
                    blocks: list[str] = []
                    for seg in segs:
                        if seg.startswith("+") and seg.endswith("leaf"):
                            comm.pruned_leaves = int(seg[1:-4])
                        else:
                            blocks.append(seg)
                    comm.symbols = _parse_block_list(blocks, files, assets)
            communities[cid] = comm

    return DecodedACN(
        mode=mode,
        root=root,
        dirs=dirs,
        files=files,
        assets=assets,
        communities=communities,
        unclustered=unclustered,
        bridge_edges=bridge_edges,
    )


# kiem thu roundtrip l0 khong mat du lieu
def test_l0_lossless_roundtrip_with_disambiguation_and_unclustered(tmp_path: Path):
    # arrange
    (tmp_path / "pkg/services").mkdir(parents=True)
    (tmp_path / "pkg/services/auth.py").write_text(
        """class BaseService:
    def ping(self):
        return 1

class UserService(BaseService):
    def init(self):
        return self.ping()

class AuthService(BaseService):
    def init(self):
        return self.ping()
""",
        encoding="utf-8",
    )
    (tmp_path / "pkg/services/runner.py").write_text(
        """from pkg.services.auth import UserService, AuthService

MODE = "fast"

def boot():
    u = UserService()
    a = AuthService()
    u.init()
    a.init()
    return MODE
""",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")

    g = GraphBuilder(tmp_path).build_full()
    comms = CommunityClusterer(seed=0).cluster(g)

    # bridge one satellite config leaf
    g.add_edge(
        "pkg.services.runner.boot",
        "pyproject_sat.project.name",
        type=EdgeType.REFERENCES.value,
    )
    g.add_node(
        "extra.isolated_leaf",
        id="extra.isolated_leaf",
        name="isolated_leaf",
        type=NodeType.CONFIG.value,
        file_path="extra.toml",
        line_start=1,
        line_end=1,
    )
    g.add_node(
        "extra.toml",
        id="extra.toml",
        name="extra.toml",
        type=NodeType.FILE.value,
        file_path="extra.toml",
    )

    # act
    wire = render_compact_wire(g, comms, "demo")
    decoded = _decode_acn2(wire)

    # assert: l0 file map
    assert decoded.mode == "L0"
    assert decoded.root == "demo"
    expected_files = {
        str(d["file_path"])
        for _, d in g.nodes(data=True)
        if d.get("file_path")
        and d.get("file_path") != "."
        and d.get("type") not in {"folder", "asset"}
    }
    assert set(decoded.files.values()) == expected_files

    # assert: intra-file disambiguation
    all_syms = [s for c in decoded.communities.values() for s in c.symbols] + decoded.unclustered
    by_short = {(decoded.files[s.fid], s.short_id): s for s in all_syms}
    assert ("pkg/services/auth.py", "UserService.init") in by_short
    assert ("pkg/services/auth.py", "AuthService.init") in by_short

    # assert: single-line range format
    mode_sym = by_short[("pkg/services/runner.py", "MODE")]
    assert mode_sym.line_start == mode_sym.line_end

    # assert: hub marker match
    for comm in comms:
        dc = decoded.communities[str(comm.id)]
        hubs = [s for s in dc.symbols if s.is_hub]
        assert len(hubs) == 1
        assert f"{hubs[0].fid}:{hubs[0].short_id}" == dc.god_ref

    # assert: bridged satellites only
    u_keys = {(decoded.files[s.fid], s.short_id) for s in decoded.unclustered}
    assert ("pyproject.toml", "name") in u_keys
    assert ("extra.toml", "isolated_leaf") not in u_keys

    # assert: bijective edge reconstruction
    op_map = {
        "^": EdgeType.INHERITS.value,
        "~": EdgeType.RENDERS.value,
        "+": EdgeType.INJECTS.value,
        ">": EdgeType.CALLS.value,
        "&": EdgeType.REFERENCES.value,
    }
    wire_types = set(op_map.values())
    decoded_edges: set[tuple[str, str, str, str]] = set()
    for s in all_syms:
        src_fp = decoded.files[s.fid]
        for op, tgt in s.edges:
            decoded_edges.add((src_fp, s.short_id, op_map[op], tgt))

    # coordinate lookup table
    coord_to_short = {
        (decoded.files[s.fid], s.line_start, s.line_end, s.type_code): s.short_id
        for s in all_syms
    }
    type_abbr = {
        "class": "c",
        "function": "f",
        "variable": "v",
        "component": "u",
        "hook": "k",
        "service": "s",
        "module": "m",
        "config": "g",
        "doc": "d",
    }
    expected_edges: set[tuple[str, str, str, str]] = set()
    for comm in comms:
        for mid in comm.members:
            md = g.nodes[mid]
            ls = int(md.get("line_start") or 1)
            le = max(ls, int(md.get("line_end") or ls))
            src_short = coord_to_short[(md["file_path"], ls, le, type_abbr[md["type"]])]
            for _, tgt, ed in g.out_edges(mid, data=True):
                et = ed.get("type")
                if et not in wire_types:
                    continue
                td = g.nodes[tgt]
                tls = int(td.get("line_start") or 1)
                tle = max(tls, int(td.get("line_end") or tls))
                tgt_short = coord_to_short[(td["file_path"], tls, tle, type_abbr[td["type"]])]
                tgt_key = f"{td['file_path']}::{tgt_short}"
                expected_edges.add((md["file_path"], src_short, et, tgt_key))
    assert decoded_edges == expected_edges


# kiem thu khong trung lap ten asset
def test_asset_basename_disambiguation(tmp_path: Path):
    # arrange
    (tmp_path / "src/themes/light").mkdir(parents=True)
    (tmp_path / "src/themes/dark").mkdir(parents=True)
    (tmp_path / "src/themes/light/icon.svg").write_text("<svg>L</svg>", encoding="utf-8")
    (tmp_path / "src/themes/dark/icon.svg").write_text("<svg>D</svg>", encoding="utf-8")
    (tmp_path / "src/Buttons.tsx").write_text(
        """import LightIcon from './themes/light/icon.svg?react';
import DarkIcon from './themes/dark/icon.svg?react';

export function LightBtn() { return <LightIcon />; }
export function DarkBtn() { return <DarkIcon />; }
""",
        encoding="utf-8",
    )

    g = GraphBuilder(tmp_path).build_full()
    comms = CommunityClusterer(seed=0).cluster(g)

    # act
    wire = render_compact_wire(g, comms, "icons")
    decoded = _decode_acn2(wire)

    # assert: distinct asset ids
    assert sorted(decoded.assets.values()) == [
        "src/themes/dark/icon.svg",
        "src/themes/light/icon.svg",
    ]
    assert len(decoded.assets) == 2
    syms = {s.short_id: s for c in decoded.communities.values() for s in c.symbols}
    assert ("~", "asset:src/themes/light/icon.svg") in syms["LightBtn"].edges
    assert ("~", "asset:src/themes/dark/icon.svg") in syms["DarkBtn"].edges


# kiem thu nen tien to thu muc @D don dieu
def test_directory_prefix_compression_monotonicity(tmp_path: Path):
    # arrange: nested frontend paths
    g = nx.MultiDiGraph()
    deep_files = [
        "src/features/checkout/components/CartSummary.tsx",
        "src/features/checkout/components/PaymentForm.tsx",
        "src/features/checkout/components/PromoInput.tsx",
        "src/features/checkout/components/ShippingStep.tsx",
        "src/features/catalog/components/ProductCard.tsx",
        "src/features/catalog/components/FilterBar.tsx",
    ]
    for fp in deep_files:
        g.add_node(fp, type=NodeType.FILE.value, file_path=fp)
    comms: list[CommunityInfo] = []

    # act
    wire = render_compact_wire(g, comms, "deep")
    decoded = _decode_acn2(wire)

    # assert: monotonic @D table reduction
    assert decoded.dirs
    assert set(decoded.files.values()) == set(deep_files)
    table_compressed = "\n".join(
        ln for ln in wire.splitlines() if ln.startswith(("@D ", "@F ", "@A "))
    )
    uncompressed_f = "@F " + ";".join(
        f"{i}:{p}" for i, p in enumerate(sorted(deep_files), start=1)
    )
    assert len(table_compressed) < len(uncompressed_f)
    assert estimate_tokens(table_compressed) <= estimate_tokens(uncompressed_f)

    # arrange: short paths omit @D
    g_short = nx.MultiDiGraph()
    g_short.add_node("a/x.py", type=NodeType.FILE.value, file_path="a/x.py")
    g_short.add_node("a/y.py", type=NodeType.FILE.value, file_path="a/y.py")
    wire_short = render_compact_wire(g_short, [], "short")
    assert "@D " not in wire_short


# kiem thu cat tia da tang theo ngan sach L0/L1/L2
def test_multi_resolution_budget_pruning_and_saliency() -> None:
    # arrange: multi-community synthetic graph
    g = nx.MultiDiGraph()
    comms: list[CommunityInfo] = []
    for c_idx in range(1, 4):
        fpath = f"src/modules/mod_{c_idx}/service_layer/handler_{c_idx}.py"
        g.add_node(fpath, type=NodeType.FILE.value, file_path=fpath)
        god = f"m{c_idx}.God{c_idx}"
        bridge = f"m{c_idx}.bridge_{c_idx}"
        g.add_node(
            god,
            type=NodeType.CLASS.value,
            name=f"God{c_idx}",
            file_path=fpath,
            line_start=1,
            line_end=40,
        )
        g.add_node(
            bridge,
            type=NodeType.FUNCTION.value,
            name=f"bridge_{c_idx}",
            file_path=fpath,
            line_start=42,
            line_end=50,
        )
        members = [god, bridge]
        for l_idx in range(1, 13):
            leaf = f"m{c_idx}.helper_worker_{c_idx}_{l_idx}"
            g.add_node(
                leaf,
                type=NodeType.FUNCTION.value,
                name=f"helper_worker_{c_idx}_{l_idx}",
                file_path=fpath,
                line_start=50 + l_idx * 3,
                line_end=52 + l_idx * 3,
            )
            g.add_edge(leaf, god, type=EdgeType.CALLS.value)
            members.append(leaf)
        comms.append(
            CommunityInfo(
                id=f"C{c_idx}",
                name=f"God{c_idx}",
                god_node=god,
                size=len(members),
                members=members,
            )
        )

    g.add_edge("m1.bridge_1", "m2.bridge_2", type=EdgeType.CALLS.value)
    g.add_edge("m2.bridge_2", "m3.bridge_3", type=EdgeType.CALLS.value)

    # act: evaluate L0 budget
    l0 = render_compact_wire(g, comms, "app")
    l0_tokens = estimate_tokens(l0)
    assert "mode:L0" in l0.splitlines()[0]

    # test L1 mid budget
    b_l1 = max(1, int(l0_tokens * 0.65))
    l1 = render_compact_wire(g, comms, "app", max_tokens=b_l1)
    d_l1 = _decode_acn2(l1)
    assert d_l1.mode == "L1"
    assert estimate_tokens(l1) <= b_l1
    assert any(c.pruned_leaves > 0 for c in d_l1.communities.values())

    # verify hub and bridge retention
    for c_idx in range(1, 4):
        names = {s.short_id for s in d_l1.communities[f"C{c_idx}"].symbols}
        assert f"God{c_idx}" in names
        assert f"bridge_{c_idx}" in names
    c1_b = next(s for s in d_l1.communities["C1"].symbols if s.short_id == "bridge_1")
    assert any(tgt.endswith("::bridge_2") for _, tgt in c1_b.edges)

    # force L2 macro skeleton
    b_l2 = 130
    l2 = render_compact_wire(g, comms, "app", max_tokens=b_l2)
    d_l2 = _decode_acn2(l2)
    assert d_l2.mode == "L2"
    assert estimate_tokens(l2) <= b_l2
    assert ("C1", "C2") in d_l2.bridge_edges
    assert ("C2", "C3") in d_l2.bridge_edges


# kiem thu khong phu thuoc xml dom
def test_xml_decoupling_invariant() -> None:
    src_path = Path("src/astermesh/serializers/compact_wire.py")
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any("xml" in mod.lower() for mod in imported)


# kiem thu khu trung lap ten sau va che do l2 cat tia canh cau
def test_deep_short_id_disambiguation_unconnected_and_tight_l2() -> None:
    g = nx.MultiDiGraph()
    g.add_node("a.py", type=NodeType.FILE.value, file_path="a.py")
    g.add_node(
        "pkg.v1.Service.run",
        name="run",
        type=NodeType.FUNCTION.value,
        file_path="a.py",
        line_start=1,
        line_end=2,
    )
    g.add_node(
        "pkg.v2.Service.run",
        name="run",
        type=NodeType.FUNCTION.value,
        file_path="a.py",
        line_start=4,
        line_end=5,
    )
    g.add_node(
        "a.isolated_cfg",
        name="isolated_cfg",
        type=NodeType.CONFIG.value,
        file_path="a.py",
        line_start=8,
        line_end=8,
    )

    comms = [
        CommunityInfo(
            id="C1",
            name="V1",
            god_node="pkg.v1.Service.run",
            size=1,
            members=["pkg.v1.Service.run"],
        ),
        CommunityInfo(
            id="C2",
            name="V2",
            god_node="pkg.v2.Service.run",
            size=1,
            members=["pkg.v2.Service.run"],
        ),
    ]
    g.add_edge("pkg.v1.Service.run", "pkg.v2.Service.run", type=EdgeType.CALLS.value)

    wire_all = render_compact_wire(g, comms, "app", include_unconnected=True)
    decoded_all = _decode_acn2(wire_all)
    short_ids = {s.short_id for c in decoded_all.communities.values() for s in c.symbols}
    assert "v1.Service.run" in short_ids
    assert "v2.Service.run" in short_ids
    assert any(s.short_id == "isolated_cfg" for s in decoded_all.unclustered)

    wire_tight = render_compact_wire(g, comms, "app", max_tokens=10)
    decoded_tight = _decode_acn2(wire_tight)
    assert decoded_tight.mode == "L2"


# L2 cap and single-community L1 budget
def test_l2_hard_cap_many_communities_and_single_community_l1_budget() -> None:
    # cap many communities under L2
    g_many = nx.MultiDiGraph()
    comms_many: list[CommunityInfo] = []
    for i in range(1, 121):
        fpath = f"src/pkg_{i}/module_{i}.py"
        g_many.add_node(fpath, type=NodeType.FILE.value, file_path=fpath)
        god = f"m{i}.Hub{i}"
        leaf = f"m{i}.leaf{i}"
        g_many.add_node(
            god,
            name=f"Hub{i}",
            type=NodeType.CLASS.value,
            file_path=fpath,
            line_start=1,
            line_end=10,
        )
        g_many.add_node(
            leaf,
            name=f"leaf{i}",
            type=NodeType.FUNCTION.value,
            file_path=fpath,
            line_start=12,
            line_end=15,
        )
        g_many.add_edge(leaf, god, type=EdgeType.CALLS.value)
        comms_many.append(
            CommunityInfo(id=f"C{i}", name=f"Hub{i}", god_node=god, size=2, members=[god, leaf])
        )
    for i in range(1, 100):
        g_many.add_edge(f"m{i}.Hub{i}", f"m{i + 1}.Hub{i + 1}", type=EdgeType.CALLS.value)
        g_many.add_edge(f"m{i}.Hub{i}", f"m{i + 2}.Hub{i + 2}", type=EdgeType.CALLS.value)

    wire_capped = render_compact_wire(g_many, comms_many, "bigapp", max_tokens=500)
    decoded_capped = _decode_acn2(wire_capped)
    assert decoded_capped.mode == "L2"
    assert estimate_tokens(wire_capped) <= 500
    assert "#omitted +" in wire_capped

    # single community keeps L1 under budget
    g_single = nx.MultiDiGraph()
    members_single: list[str] = []
    hub_id = "feat.main.MainHub"
    hub_file = "src/feature_area/core_module/main_hub_component.ts"
    g_single.add_node(hub_file, type=NodeType.FILE.value, file_path=hub_file)
    g_single.add_node(
        hub_id,
        name="MainHub",
        type=NodeType.COMPONENT.value,
        file_path=hub_file,
        line_start=1,
        line_end=50,
    )
    members_single.append(hub_id)
    for idx in range(1, 50):
        fpath = f"src/feature_area/sub_pkg_{idx}/widget_panel_view_{idx}.ts"
        nid = f"feat.sub_{idx}.Widget{idx}"
        g_single.add_node(fpath, type=NodeType.FILE.value, file_path=fpath)
        g_single.add_node(
            nid,
            name=f"Widget{idx}",
            type=NodeType.COMPONENT.value,
            file_path=fpath,
            line_start=1,
            line_end=20,
        )
        g_single.add_edge(nid, hub_id, type=EdgeType.CALLS.value)
        members_single.append(nid)
    for idx in range(1, 6):
        aid = f"assets/icon_{idx}.svg"
        g_single.add_node(aid, type=NodeType.ASSET.value, file_path=aid)
        g_single.add_edge("feat.sub_49.Widget49", aid, type=EdgeType.REFERENCES.value)

    c1 = CommunityInfo(
        id="C1", name="MainHub", god_node=hub_id, size=len(members_single), members=members_single
    )
    wire_c1 = render_compact_wire(g_single, [c1], "feapp", max_tokens=600)
    decoded_c1 = _decode_acn2(wire_c1)
    assert decoded_c1.mode == "L1"
    assert estimate_tokens(wire_c1) <= 600
    assert len(decoded_c1.communities["C1"].symbols) >= 1
    assert len(decoded_c1.files) < 50
