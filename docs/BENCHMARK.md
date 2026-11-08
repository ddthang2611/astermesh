# Astermesh Token Benchmark Guide

This guide explains how to benchmark and verify token savings and context efficiency when using **Astermesh** compared to naive raw codebase dumps.

---

## 1. Why Benchmark?

When large codebases are fed into Large Language Models (LLMs), feeding raw source files introduces:
1. **Severe Token Bloat**: Thousands of tokens wasted on implementation bodies, boilerplate, whitespace, and comments.
2. **Context Window Saturation**: Quick exhaustion of 128k/200k context limits, degrading attention mechanism quality.
3. **Escalated API Costs**: Redundant tokens billed repeatedly across multi-turn developer prompts.

**Astermesh** extracts deterministic logic entities (classes, methods, functions, variables), preserves physical line ranges (`[line_start, line_end]`), and clusters them via the **Leiden community algorithm** into a compact Semantic XML.

---

## 2. Quick Start: Benchmark Your Own Codebase

Clone this repository and run the benchmark directly against any local repository:

```bash
# 1. Install Astermesh in your environment
git clone https://github.com/your-username/astermesh.git
cd astermesh
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# 2. Run benchmark on your target project
astermesh benchmark /path/to/your/project --report benchmark_report.md
```

---

## 3. How to Interpret the Benchmark Metrics

The generated report (`benchmark_report.md` or terminal output) provides quantitative indicators:

### Metric Explanations

- **Raw Codebase Dump**: Total token count if all source files in the project are concatenated and passed directly into the LLM prompt.
- **Astermesh Semantic XML**: Token count of the generated `xml-graph/context_graph.xml`, containing structural topology, FQNs, line coordinates, and call relationships.
- **Improvement / Savings Percentage**:
  Typical projects see between **70% to 90%** reduction in total tokens.
- **Single Community Average**: Average token size for an isolated architectural community. Using `get_community(name)` delivers micro-context with only a few hundred tokens.
- **Cost Savings (Estimated)**: Financial savings per 1,000 queries calculated at the standard reference price of \$3.00 / 1M input tokens.

---

## 4. Benchmark Methodology & Precision

Astermesh ensures high-fidelity benchmarks:
- **Tokenizer**: Uses OpenAI `tiktoken` with the `cl100k_base` BPE tokenizer (used by GPT-4, Claude 3.x estimation, and modern LLM coding engines). Falls back to standard character-based estimation (`len(text) // 4`) if `tiktoken` is not installed.
- **Ignored Files**: Automatically skips `.git`, `.venv`, `node_modules`, `dist`, `build`, and non-source binaries to prevent artificial token inflation.
- **In-Memory Execution**: Benchmark renders Semantic XML entirely in-memory without polluting the target repository with temporary files.
