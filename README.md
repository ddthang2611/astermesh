# Astermesh

![Astermesh Logo](docs/assets/astermesh-logo.jpg)

Deterministic Code Knowledge Graph & Local Context Layer (MCP Server) for AI Coding Assistants.

> **Official AI Agent Skill**: Install the production Graph-First Navigation & Blast-Radius Refactoring Skill for Claude Code, Cursor, Windsurf, Copilot, and Antigravity on Agensi:  
> **[Get Astermesh Code Knowledge Graph & MCP on Agensi.io](https://www.agensi.io/skills/astermesh-code-knowledge-graph-mcp)**

---

## 1. Overview

Astermesh provides a **100% deterministic, static code intelligence layer** designed for LLMs, pair-programming assistants, and human developers:
- **Zero Vector Embeddings & Zero LLM Calls for Indexing**: Built purely on static AST parsing with `tree-sitter` and in-memory graph algorithms with `networkx`, `igraph`, and `leidenalg`.
- **Polyglot & Modern Web Ecosystem**: Supports Python, TypeScript, JavaScript, React/JSX, Vue SFC, Svelte SFC, Angular, NestJS DI and providers, satellite configuration and documentation files, and isolated static assets.
- **Dual Perspective**:
  - **For LLMs**: Dense `#ACN2` wire notation structured by Leiden community clusters with optional `L0/L1/L2` token budgeting.
  - **For Humans**: Interactive 2D topological graph visualizations in standalone HTML.
- **Absolute Line Precision**: Replaces blind `grep`/regex searches with exact physical file paths and 1-based line intervals `[line_start, line_end]`.
- **Real-Time Incremental Updates**: In-memory graph synchronizes delta changes in milliseconds upon file save via asynchronous file watching.

### Verified Performance & Token Compression Benchmarks

Measured across production Python, React/Next.js, and NestJS/TypeScript repositories:

| Metric | Production Benchmark Result |
| :--- | :--- |
| **Token Reduction: `#ACN2` vs Raw Dump** | **89.07% – 98.30% saved**, compressing `234,466` raw tokens down to `3,997` `#ACN2 L2` tokens |
| **Indexing Throughput** | **87 – 113 files/sec** and `6,800 – 9,500 LOC/sec`, completing full build and Leiden clustering in `1.2s – 2.3s` |
| **Peak Memory Overhead** | **6.2 MB – 12.6 MB RAM** |
| **Incremental File-Save Sync** | **0.37 ms – 0.70 ms** per modified file |
| **MCP Query Latency `P50`** | **0.21 ms – 0.71 ms** across symbol location, neighbor lookup, blast-radius analysis, and path tracing |

For comprehensive documentation, see the [User Guide](docs/guide.md) and the [Official Agensi Skill Listing](https://www.agensi.io/skills/astermesh-code-knowledge-graph-mcp).

---

## 2. Installation

Requires Python `>= 3.10`:

```bash
# Clone and setup virtual environment
git clone https://github.com/ddthang2611/astermesh.git
cd astermesh
python3 -m venv .venv
source .venv/bin/activate

# Install in editable mode
pip install -e .
```

---

## 3. Usage

### A. CLI Snapshot Mode

Analyze a repository and export `#ACN2` wire topology (for LLMs), architecture report, and interactive 2D HTML (for humans) into `astermesh-graph/`:

```bash
# Build artifacts into default astermesh-graph/ directory
astermesh build .

# Build with a strict token budget (triggers L0 -> L1 -> L2 pruning)
astermesh build . --max-tokens 6000

# Build for another target repository
astermesh build /path/to/target/repo

# Skip HTML visualization or also emit deprecated legacy XML
astermesh build . --no-html
astermesh build . --include-xml-legacy
```

Generated artifacts in `astermesh-graph/`:
- `astermesh-graph/context_graph.acn`: Dense `#ACN2` community topology for LLM attention context.
- `astermesh-graph/GRAPH_REPORT.md`: Macro architecture summary for LLM working memory.
- `astermesh-graph/astermesh_view.html`: Standalone interactive 2D network map.

Locate symbols and physical lines directly from the terminal:
```bash
astermesh query "GraphBuilder"

# Overview statistics or list detected communities
astermesh query --overview
astermesh query --list-communities
```

### B. MCP Daemon Server Mode (AI Pair Programming)

Launch the Model Context Protocol (MCP 2.x) daemon server with live incremental file watching:

```bash
# Standard I/O transport (Cursor, Claude Desktop, Antigravity)
astermesh serve . --transport stdio

# SSE HTTP transport
astermesh serve . --transport sse --host 127.0.0.1 --port 8000
```

#### MCP Client Configuration (`mcpServers`)

Add to your Cursor or Claude Desktop MCP configuration (`mcp.json`):

```json
{
  "mcpServers": {
    "astermesh": {
      "command": "astermesh",
      "args": ["serve", "/path/to/your/project", "--transport", "stdio"]
    }
  }
}
```

### C. Self-Benchmark Mode

Quantify token savings and context efficiency on your own project:

```bash
astermesh benchmark /path/to/your/project --report benchmark_report.md
```

See [Benchmark Guide](docs/BENCHMARK.md) for full benchmark methodology and cost calculations.

---

## 4. MCP Tools & Resources

### Available Tools:
- `locate_symbol(name: str)`: Resolves physical file paths and exact line intervals `[line_start, line_end]`.
- `get_file_outline(file_path: str)`: Extracts complete structural outline of classes, functions, and variables in a file.
- `get_neighbors(symbol: str, direction: str = "both")`: Inspects 1-hop callers (`in`) and callees (`out`) without reading entire files.
- `get_impact_radius(symbol: str, max_depth: int = 2)`: Computes blast radius and transitive callers to assess regression risks.
- `trace_path(source: str, target: str)`: Computes the shortest interaction or dependency path between two logic entities.
- `get_community(name: str)`: Retrieves all entities in a functional community identified by the Leiden algorithm.
- `get_community_wire(name: str, max_tokens: int | None = None)`: Renders a single functional community in standalone `#ACN2` wire notation.
- `read_file_snippet(file_path: str, start_line: int = 1, end_line: int | None = None)`: Reads exact physical line slices from workspace files.
- `rebuild_graph()`: Triggers a full re-scan and graph re-indexing in memory.

### Available Resources:
- `codebase://context`: Dense `#ACN2` wire representation of the repository graph (supports `--context-max-tokens`).
- `codebase://report`: Macro architecture snapshot (statistics, Leiden communities, God nodes, and dependency cycles).
- `codebase://communities`: Summary list of all detected functional modules.

---

## 5. Documentation & Marketplace

- [Official Agent Skill on Agensi.io](https://www.agensi.io/skills/astermesh-code-knowledge-graph-mcp): Ready-to-install `SKILL.md` workflow package for Claude Code, Cursor, Copilot, and 20+ coding agents.
- [User Guide](docs/guide.md): Complete guide to CLI, MCP, Python API, and troubleshooting.
- [Benchmark Guide](docs/BENCHMARK.md): Token efficiency methodology and savings calculation.
- [AI Agent Rules](.astermesh/AGENTS.md): Graph-First navigation protocol for LLM assistants.

---

## 6. License & Legal Compliance

- **License**: [MIT License](LICENSE)
- **Third-Party Notices**: [NOTICE](NOTICE)
- **Privacy & Data Protection**: [PRIVACY.md](PRIVACY.md)
