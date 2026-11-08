import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from astermesh.graph.builder import GraphBuilder, is_ignored_dir
from astermesh.graph.clustering import CommunityClusterer
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
    num_communities: int
    avg_community_tokens: int
    savings_percentage: float
    cost_saved_usd_per_1k: float


# token benchmark execution engine
class TokenBenchmarkRunner:
    def __init__(self, target_path: Path) -> None:
        self.target_path = target_path.resolve()
        self._encoder: Any = None
        try:
            import tiktoken
            self._encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self._encoder = None

    def count_tokens(self, text: str) -> int:
        if self._encoder:
            try:
                return len(self._encoder.encode(text))
            except Exception:
                pass
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
                fpath = curr / fname
                if fpath.suffix.lower() in supported_exts:
                    raw_files += 1
                    try:
                        raw_chunks.append(fpath.read_text(encoding="utf-8", errors="replace"))
                    except OSError:
                        continue

        raw_text = "\n".join(raw_chunks)
        raw_tokens = self.count_tokens(raw_text)

        # construct graph and render semantic xml in memory
        graph = builder.build_full()
        clusterer = CommunityClusterer()
        communities = clusterer.cluster(graph)

        xml_str = render_semantic_xml(graph, communities, self.target_path.name)
        xml_tokens = self.count_tokens(xml_str)
        savings = (1.0 - (xml_tokens / raw_tokens)) * 100.0 if raw_tokens > 0 else 0.0

        avg_comm = 0
        if communities:
            avg_comm = (
                sum(self.count_tokens(str(c.members)) for c in communities) // len(communities)
            )

        cost_per_million = 3.0
        saved_usd = ((raw_tokens - xml_tokens) * 1000 / 1_000_000) * cost_per_million

        return BenchmarkMetrics(
            target_name=self.target_path.name,
            total_files=raw_files,
            raw_tokens=raw_tokens,
            raw_characters=len(raw_text),
            astermesh_xml_tokens=xml_tokens,
            astermesh_xml_characters=len(xml_str),
            num_communities=len(communities),
            avg_community_tokens=avg_comm,
            savings_percentage=savings,
            cost_saved_usd_per_1k=saved_usd,
        )

    def format_markdown_report(self, m: BenchmarkMetrics) -> str:
        char_savings = (
            (1.0 - m.astermesh_xml_characters / max(1, m.raw_characters)) * 100.0
            if m.raw_characters > 0
            else 0.0
        )
        return (
            f"# Astermesh Token Benchmark Report\n\n"
            f"## Target Codebase: `{m.target_name}`\n"
            f"- Analyzed Source Files: {m.total_files}\n"
            f"- Communities Discovered (Leiden): {m.num_communities}\n\n"
            f"## 1. Context Size Comparison\n"
            f"| Metric | Raw Codebase Dump | Astermesh Semantic XML | Improvement |\n"
            f"| :--- | :--- | :--- | :--- |\n"
            f"| **Token Count** | {m.raw_tokens:,} tokens | {m.astermesh_xml_tokens:,} tokens | **{m.savings_percentage:.2f}% Saved** |\n"
            f"| **Character Count** | {m.raw_characters:,} chars | {m.astermesh_xml_characters:,} chars | {char_savings:.2f}% Saved |\n"
            f"| **Single Community Avg** | N/A | ~{m.avg_community_tokens:,} tokens | Micro-context ready |\n\n"
            f"## 2. LLM Cost & Context Efficiency\n"
            f"- **Token Compression**: Astermesh strips implementation bodies and comments, preserving exact AST physical coordinates `[line_start, line_end]` and call graphs.\n"
            f"- **Estimated Savings**: ~${m.cost_saved_usd_per_1k:.2f} USD per 1,000 architectural queries (calculated at $3.00 / 1M input tokens).\n"
        )
