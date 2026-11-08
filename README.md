# Astermesh

Deterministic Code Knowledge Graph & Local Context Layer (MCP Server) for AI Coding Assistants.

---

## 1. Overview

Astermesh provides a **100% deterministic, static code intelligence layer** designed for LLMs, pair-programming assistants (Cursor, Claude Desktop, Antigravity), and human developers:
- **Zero Vector Embeddings & Zero LLM Calls for Indexing**: Built purely on static AST parsing (`tree-sitter`) and in-memory graph algorithms (`networkx`, `igraph`, `leidenalg`).
- **Dual Perspective**:
  - **For LLMs**: Compact Semantic XML structured by Leiden community clusters to optimize attention mechanisms and eliminate token bloat.
  - **For Humans**: Interactive 2D topological graph visualizations in standalone HTML.
- **Absolute Line Precision**: Replaces blind `grep`/regex searches with exact physical file paths and 1-based line intervals `[line_start, line_end]`.
- **Real-Time Incremental Updates**: In-memory graph synchronizes delta changes in milliseconds upon file save via asynchronous file watching.

For comprehensive documentation, see the [User Guide](docs/guide.md).

---

## 2. Installation

Requires Python `>= 3.10`:

```bash
# Clone and setup virtual environment
git clone https://github.com/your-username/astermesh.git
cd astermesh
python3 -m venv .venv
source .venv/bin/activate

# Install in editable mode
pip install -e .
```

---

## 3. Usage

### A. CLI Snapshot Mode

Analyze a repository and export Semantic XML (for LLMs), architecture report, and interactive 2D HTML (for humans) into `xml-graph/`:

```bash
# Build artifacts into default xml-graph/ directory
astermesh build .

# Build for another target repository
astermesh build /path/to/target/repo

# Skip HTML visualization (export XML & Markdown report only)
astermesh build . --no-html
```

Generated artifacts in `xml-graph/`:
- `xml-graph/context_graph.xml`: Compact community topology for LLM attention context.
- `xml-graph/GRAPH_REPORT.md`: Macro architecture summary for LLM working memory.
- `xml-graph/astermesh_view.html`: Standalone interactive 2D network map.

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
- `get_community(name: str)`: Retrieves all entities in an architectural community identified by the Leiden algorithm.
- `rebuild_graph()`: Triggers a full re-scan and graph re-indexing in memory.

### Available Resources:
- `codebase://report`: Macro architecture snapshot (statistics, Leiden communities, God nodes, and dependency cycles).
- `codebase://communities`: Summary list of all detected functional modules.

---

## 5. Documentation

- [User Guide](docs/guide.md): Complete guide to CLI, MCP, Python API, and troubleshooting.
- [Benchmark Guide](docs/BENCHMARK.md): Token efficiency methodology and savings calculation.
- [AI Agent Rules](.astermesh/AGENTS.md): Graph-First navigation protocol for LLM assistants.

---

## 6. License

[MIT License](LICENSE).
