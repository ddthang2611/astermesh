import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astermesh.graph.builder import GraphBuilder
from astermesh.graph.clustering import CommunityClusterer
from astermesh.graph.filters import is_ignored_dir, is_ignored_file
from astermesh.serializers.compact_wire import render_compact_wire
from astermesh.serializers.xml_context import render_semantic_xml


# benchmark data container
@dataclass
class BenchmarkMetrics:
    target_name: str
    total_files: int
    raw_tokens: int
    raw_characters: int
    astermesh_xml_tokens: int
    astermesh_xml_characters: int
    astermesh_acn_tokens: int
    astermesh_acn_characters: int
    xml_lines: int
    acn_lines: int
    num_communities: int
    avg_community_tokens: int
    savings_percentage: float
    acn_savings_percentage: float
    cost_saved_usd_per_1k: float


# token benchmark execution engine
class TokenBenchmarkRunner:
    def __init__(self, target_path: Path) -> None:
        self.target_path = target_path.resolve()
        self._encoder: Any = None
        try:
            import tiktoken

            self._encoder = tiktoken.get_encoding("cl100k_base")
        except (ImportError, ValueError, RuntimeError):
            self._encoder = None

    def count_tokens(self, text: str) -> int:
        if self._encoder:
            try:
                return len(self._encoder.encode(text))
            except (ValueError, RuntimeError):
                return max(1, len(text) // 4)
        return max(1, len(text) // 4)

    def run(self) -> BenchmarkMetrics:
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

        graph = builder.build_full()
        clusterer = CommunityClusterer()
        communities = clusterer.cluster(graph)

        xml_str = render_semantic_xml(graph, communities, self.target_path.name)
        xml_tokens = self.count_tokens(xml_str)
        xml_savings = (1.0 - (xml_tokens / raw_tokens)) * 100.0 if raw_tokens > 0 else 0.0

        acn_str = render_compact_wire(graph, communities, self.target_path.name)
        acn_tokens = self.count_tokens(acn_str)
        acn_savings = (1.0 - (acn_tokens / raw_tokens)) * 100.0 if raw_tokens > 0 else 0.0

        avg_comm = 0
        if communities:
            avg_comm = (
                sum(self.count_tokens(str(c.members)) for c in communities) // len(communities)
            )

        cost_per_million = 3.0
        saved_usd = ((raw_tokens - acn_tokens) * 1000 / 1_000_000) * cost_per_million

        return BenchmarkMetrics(
            target_name=self.target_path.name,
            total_files=raw_files,
            raw_tokens=raw_tokens,
            raw_characters=len(raw_text),
            astermesh_xml_tokens=xml_tokens,
            astermesh_xml_characters=len(xml_str),
            astermesh_acn_tokens=acn_tokens,
            astermesh_acn_characters=len(acn_str),
            xml_lines=len(xml_str.splitlines()),
            acn_lines=len(acn_str.splitlines()),
            num_communities=len(communities),
            avg_community_tokens=avg_comm,
            savings_percentage=xml_savings,
            acn_savings_percentage=acn_savings,
            cost_saved_usd_per_1k=saved_usd,
        )

    def format_markdown_report(self, m: BenchmarkMetrics) -> str:
        acn_char_savings = (
            (1.0 - m.astermesh_acn_characters / max(1, m.raw_characters)) * 100.0
            if m.raw_characters > 0
            else 0.0
        )
        return (
            f"# Astermesh Token Benchmark Report\n\n"
            f"## Target Codebase: `{m.target_name}`\n"
            f"- Analyzed Source Files: {m.total_files}\n"
            f"- Communities Discovered (Leiden): {m.num_communities}\n\n"
            f"## 1. Context Size Comparison\n"
            f"| Metric | Raw Codebase | Compact XML | Dense ACN Wire | ACN vs Raw |\n"
            f"| :--- | :--- | :--- | :--- | :--- |\n"
            f"| **Token Count** | {m.raw_tokens:,} | {m.astermesh_xml_tokens:,} | "
            f"{m.astermesh_acn_tokens:,} | **{m.acn_savings_percentage:.2f}% Saved** |\n"
            f"| **Character Count** | {m.raw_characters:,} | {m.astermesh_xml_characters:,} | "
            f"{m.astermesh_acn_characters:,} | {acn_char_savings:.2f}% Saved |\n"
            f"| **Line Count** | N/A | {m.xml_lines:,} lines | "
            f"{m.acn_lines:,} lines | Minimal lines |\n"
            f"| **Single Community Avg** | N/A | ~{m.avg_community_tokens:,} tokens | "
            f"~{m.avg_community_tokens:,} tokens | Micro-context ready |\n\n"
            f"## 2. LLM Cost & Context Efficiency\n"
            f"- **Note**: This benchmark measures full-repository architectural context compression vs raw source dump (as opposed to targeted single-symbol grep).\n"
            f"- **Estimated Savings**: ~${m.cost_saved_usd_per_1k:.2f} USD per 1,000 full-context queries (at $3.00 / 1M input tokens).\n"
        )
