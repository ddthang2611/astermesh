import os
import warnings
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

from astermesh.graph.builder import GraphBuilder
from astermesh.graph.clustering import CommunityClusterer
from astermesh.graph.filters import is_ignored_dir, is_ignored_file
from astermesh.graph.model import CommunityInfo, EdgeType, NodeType
from astermesh.serializers.compact_wire import estimate_tokens, render_compact_wire
from astermesh.serializers.xml_context import render_semantic_xml

_LEGACY_TYPE_ABBR: dict[str, str] = {
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

_LEGACY_EDGE_OPS: tuple[tuple[str, str], ...] = (
    (EdgeType.INHERITS.value, "^"),
    (EdgeType.RENDERS.value, "~"),
    (EdgeType.INJECTS.value, "+"),
    (EdgeType.CALLS.value, ">"),
    (EdgeType.REFERENCES.value, "&"),
)


# legacy acn1 comparison baseline
def _render_acn1_baseline(
    graph: nx.MultiDiGraph,
    communities: list[CommunityInfo],
    root_name: str,
) -> str:
    file_map: dict[str, str] = {}
    file_entries: list[str] = []
    excluded = {NodeType.FOLDER.value, NodeType.ASSET.value}
    for _, data in graph.nodes(data=True):
        fpath = data.get("file_path")
        if fpath and fpath != "." and data.get("type") not in excluded and fpath not in file_map:
            fid = str(len(file_map) + 1)
            file_map[str(fpath)] = fid
            file_entries.append(f"{fid}:{fpath}")

    lines: list[str] = [
        (
            f"#ACN1 root:{root_name} "
            "t={c:cls,f:fn,v:var,u:ui,k:hook,s:svc,m:mod,g:cfg,d:doc} "
            "ops={^:inh,~:ui,+:inj,>:call,&:ref}"
        )
    ]
    if file_entries:
        lines.append("@F " + ";".join(file_entries))

    for comm in communities:
        grouped: dict[str, list[str]] = {}
        for mid in comm.members:
            if not graph.has_node(mid):
                continue
            ndata = graph.nodes[mid]
            fid = file_map.get(str(ndata.get("file_path", "")), "0")
            short_id = str(ndata.get("name") or mid.split(".")[-1])
            ntype = _LEGACY_TYPE_ABBR.get(str(ndata.get("type", "")), "e")
            l_start = ndata.get("line_start") or 1
            l_end = ndata.get("line_end") or l_start

            buckets: dict[str, list[str]] = {et: [] for et, _ in _LEGACY_EDGE_OPS}
            for _, tgt, edata in graph.out_edges(mid, data=True):
                et = str(edata.get("type", ""))
                if et not in buckets or not graph.has_node(tgt):
                    continue
                tdata = graph.nodes[tgt]
                t_fid = file_map.get(str(tdata.get("file_path", "")), "")
                t_name = str(tdata.get("name") or str(tgt).split(".")[-1])
                buckets[et].append(t_name if (not t_fid or t_fid == fid) else f"{t_fid}:{t_name}")

            ops = "".join(
                f"{op}{','.join(buckets[et])}" for et, op in _LEGACY_EDGE_OPS if buckets[et]
            )
            grouped.setdefault(fid, []).append(f"{short_id}:{ntype}@{l_start}-{l_end}{ops}")
        blocks = [f"{fid}[{';'.join(recs)}]" for fid, recs in grouped.items()]
        if blocks:
            lines.append(f"@{comm.id} {comm.name}|{'|'.join(blocks)}")

    return "\n".join(lines) + "\n"


def _extract_acn_mode(acn_text: str) -> str:
    first_line = acn_text.split("\n", 1)[0]
    for part in first_line.split():
        if part.startswith("mode:"):
            return part.split(":", 1)[1]
    return "L0"


# benchmark data container
@dataclass
class BenchmarkMetrics:
    target_name: str
    total_files: int
    raw_tokens: int
    raw_characters: int
    astermesh_xml_tokens: int | None
    astermesh_xml_characters: int | None
    astermesh_acn_tokens: int
    astermesh_acn_characters: int
    xml_lines: int | None
    acn_lines: int
    num_communities: int
    avg_community_tokens: int
    savings_percentage: float | None
    acn_savings_percentage: float
    cost_saved_usd_per_1k: float
    acn_mode: str = "L0"
    acn1_tokens: int = 0
    acn1_characters: int = 0
    acn1_lines: int = 0
    acn_vs_acn1_savings_percentage: float = 0.0
    acn_vs_xml_savings_percentage: float | None = None
    acn_char_savings_percentage: float = 0.0


# token benchmark execution engine
class TokenBenchmarkRunner:
    def __init__(self, target_path: Path) -> None:
        self.target_path = target_path.resolve()

    def count_tokens(self, text: str) -> int:
        return estimate_tokens(text)

    def run(
        self,
        max_tokens: int | None = None,
        include_xml_legacy: bool = False,
    ) -> BenchmarkMetrics:
        builder = GraphBuilder(self.target_path)
        supported_exts = builder.parser.supported_extensions

        # collect raw codebase files
        raw_files = 0
        raw_chunks: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.target_path):
            dirnames[:] = [d for d in dirnames if not is_ignored_dir(d)]
            curr = Path(dirpath)
            for fname in filenames:
                if is_ignored_file(fname):
                    continue
                fpath = curr / fname
                if fpath.suffix.lower() in supported_exts:
                    raw_files += 1
                    try:
                        raw_chunks.append(fpath.read_text(encoding="utf-8", errors="replace"))
                    except OSError:
                        continue

        raw_text = "\n".join(raw_chunks)
        raw_tokens = self.count_tokens(raw_text)
        raw_chars = len(raw_text)

        graph = builder.build_full()
        clusterer = CommunityClusterer()
        communities = clusterer.cluster(graph)

        acn_str = render_compact_wire(
            graph, communities, self.target_path.name, max_tokens=max_tokens
        )
        acn_tokens = self.count_tokens(acn_str)
        acn_chars = len(acn_str)
        acn_lines = len(acn_str.splitlines())
        acn_mode = _extract_acn_mode(acn_str)
        acn_savings = (1.0 - (acn_tokens / raw_tokens)) * 100.0 if raw_tokens > 0 else 0.0
        acn_char_savings = (1.0 - (acn_chars / raw_chars)) * 100.0 if raw_chars > 0 else 0.0

        acn1_str = _render_acn1_baseline(graph, communities, self.target_path.name)
        acn1_tokens = self.count_tokens(acn1_str)
        acn_vs_acn1 = (1.0 - (acn_tokens / acn1_tokens)) * 100.0 if acn1_tokens > 0 else 0.0

        xml_tokens: int | None = None
        xml_chars: int | None = None
        xml_lines: int | None = None
        xml_savings: float | None = None
        acn_vs_xml: float | None = None
        if include_xml_legacy:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                xml_str = render_semantic_xml(graph, communities, self.target_path.name)
            xml_tokens = self.count_tokens(xml_str)
            xml_chars = len(xml_str)
            xml_lines = len(xml_str.splitlines())
            xml_savings = (1.0 - (xml_tokens / raw_tokens)) * 100.0 if raw_tokens > 0 else 0.0
            acn_vs_xml = (1.0 - (acn_tokens / xml_tokens)) * 100.0 if xml_tokens > 0 else 0.0

        # measure actual community wire size
        comm_lines = [
            ln
            for ln in acn_str.splitlines()
            if ln.startswith("@") and not ln.startswith(("@D ", "@F ", "@A ", "@U ", "@E "))
        ]
        avg_comm = (
            sum(self.count_tokens(ln) for ln in comm_lines) // len(comm_lines)
            if comm_lines
            else 0
        )

        cost_per_million = 3.0
        saved_usd = ((raw_tokens - acn_tokens) * 1000 / 1_000_000) * cost_per_million

        return BenchmarkMetrics(
            target_name=self.target_path.name,
            total_files=raw_files,
            raw_tokens=raw_tokens,
            raw_characters=raw_chars,
            astermesh_xml_tokens=xml_tokens,
            astermesh_xml_characters=xml_chars,
            astermesh_acn_tokens=acn_tokens,
            astermesh_acn_characters=acn_chars,
            xml_lines=xml_lines,
            acn_lines=acn_lines,
            num_communities=len(communities),
            avg_community_tokens=avg_comm,
            savings_percentage=xml_savings,
            acn_savings_percentage=acn_savings,
            cost_saved_usd_per_1k=saved_usd,
            acn_mode=acn_mode,
            acn1_tokens=acn1_tokens,
            acn1_characters=len(acn1_str),
            acn1_lines=len(acn1_str.splitlines()),
            acn_vs_acn1_savings_percentage=acn_vs_acn1,
            acn_vs_xml_savings_percentage=acn_vs_xml,
            acn_char_savings_percentage=acn_char_savings,
        )

    def format_markdown_report(self, m: BenchmarkMetrics) -> str:
        xml_tok = (
            f"{m.astermesh_xml_tokens:,}"
            if m.astermesh_xml_tokens is not None
            else "N/A (opt-in)"
        )
        xml_chr = (
            f"{m.astermesh_xml_characters:,}"
            if m.astermesh_xml_characters is not None
            else "N/A (opt-in)"
        )
        xml_ln = f"{m.xml_lines:,} lines" if m.xml_lines is not None else "N/A (opt-in)"
        xml_cmp = (
            f" | {m.acn_vs_xml_savings_percentage:.2f}% smaller than XML"
            if m.acn_vs_xml_savings_percentage is not None
            else ""
        )
        return (
            f"# Astermesh Token Benchmark Report\n\n"
            f"## Target Codebase: `{m.target_name}`\n"
            f"- Analyzed Source Files: {m.total_files}\n"
            f"- Communities Discovered (Leiden): {m.num_communities}\n"
            f"- Wire Resolution Mode: `#ACN2` (`{m.acn_mode}`)\n\n"
            f"## 1. Context Size Comparison\n"
            f"| Metric | Raw Codebase | Compact XML | #ACN1 Wire | "
            f"Dense ACN Wire (`#ACN2`) | ACN vs Raw |\n"
            f"| :--- | :--- | :--- | :--- | :--- | :--- |\n"
            f"| **Token Count** | {m.raw_tokens:,} | {xml_tok} | {m.acn1_tokens:,} | "
            f"{m.astermesh_acn_tokens:,} | **{m.acn_savings_percentage:.2f}% Saved** |\n"
            f"| **Character Count** | {m.raw_characters:,} | {xml_chr} | {m.acn1_characters:,} | "
            f"{m.astermesh_acn_characters:,} | {m.acn_char_savings_percentage:.2f}% Saved |\n"
            f"| **Line Count** | N/A | {xml_ln} | {m.acn1_lines:,} lines | "
            f"{m.acn_lines:,} lines | Minimal lines |\n"
            f"| **Single Community Avg** | N/A | N/A | N/A | "
            f"~{m.avg_community_tokens:,} tokens | Micro-context ready |\n\n"
            f"## 2. LLM Cost & Context Efficiency\n"
            f"- **Wire Efficiency**: `{m.acn_vs_acn1_savings_percentage:.2f}%` "
            f"token reduction vs `#ACN1`{xml_cmp}.\n"
            f"- **Note**: This benchmark measures full-repository architectural context "
            f"compression vs raw source dump (as opposed to targeted single-symbol grep).\n"
            f"- **Estimated Savings**: ~${m.cost_saved_usd_per_1k:.2f} USD per 1,000 "
            f"full-context queries (at $3.00 / 1M input tokens).\n"
        )
