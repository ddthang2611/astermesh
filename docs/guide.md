# Astermesh User Guide

Astermesh turns a source repository into an in-memory code knowledge graph using static AST parsing (`tree-sitter`) and graph algorithms (`networkx`, `igraph`, `leidenalg`). The graph is exposed in three ways:

| Interface | Audience | Purpose |
| :--- | :--- | :--- |
| `astermesh build` | LLM / human | Export snapshot artifacts (Semantic XML, Markdown report, HTML map) |
| `astermesh query` | Human | Look up symbols and communities from the terminal |
| `astermesh serve` | AI assistant | Long-running MCP server with live incremental updates |

No embeddings and no LLM calls are used during indexing.

---

## 1. Installation

Requirements: Python 3.10+.

```bash
git clone https://github.com/your-username/astermesh.git
cd astermesh
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Verify:

```bash
astermesh --help
# or, without relying on the console script
python -m astermesh --help
```

---

## 2. Core Concepts

### 2.1 Supported languages

| Extension | Extracted entities | Extracted relations |
| :--- | :--- | :--- |
| `.py` | classes, functions, methods, module-level variables | imports, inheritance, calls, variable references |
| `.js`, `.ts`, `.tsx` | top-level function declarations, class declarations | calls |

Other file types are ignored.

### 2.2 Ignored directories

The following directories are skipped during scanning and watching, together with every directory whose name starts with `.`:

`.git`, `.venv`, `venv`, `__pycache__`, `node_modules`, `.agents`, `.pytest_cache`, `.mypy_cache`, `dist`, `build`

### 2.3 Node identifiers

Every node in the graph has a stable string ID:

| Node type | ID format | Example |
| :--- | :--- | :--- |
| `folder` | path relative to the workspace root (`.` for root) | `src/astermesh/graph` |
| `file` | path relative to the workspace root | `src/astermesh/graph/builder.py` |
| `class`, `function`, `variable` | fully qualified name derived from the file path | `src.astermesh.graph.builder.GraphBuilder.build_full` |

FQN rules: the file extension is dropped, `/` becomes `.`, and `__init__` segments are removed. FQNs are therefore relative to the directory you index: indexing the repository root yields `src.astermesh...`, indexing `src/` yields `astermesh...`.

### 2.4 Edge types

| Edge | Meaning |
| :--- | :--- |
| `CONTAINS` | folder contains folder or file |
| `DEFINES` | file or class defines an entity |
| `IMPORTS` | file imports a module or symbol |
| `CALLS` | entity calls another entity |
| `INHERITS` | class inherits from another class |
| `REFERENCES` | entity reads a module-level variable |

Call and import targets are resolved statically in this order: same-module symbol, imported alias, exact FQN, then a unique short name across the project. Ambiguous short names are left unresolved rather than guessed.

### 2.5 Communities

Logic entities (classes, functions, variables) are clustered with the Leiden algorithm (modularity objective). Each community is named after its **God Node**, the member with the highest degree centrality, and receives an ID `C1`, `C2`, ... ordered by size.

---

## 3. CLI Reference

### 3.1 `astermesh build`

Index a repository and write snapshot artifacts.

```bash
astermesh build [PATH] [-o OUTPUT] [--html HTML_PATH] [--no-html]
```

| Option | Default | Description |
| :--- | :--- | :--- |
| `PATH` | `.` | Workspace root to index |
| `-o`, `--output`, `--output-dir` | `xml-graph` | Output directory, or an explicit `.xml` file path. Relative paths are resolved against `PATH` |
| `--html` | `<output>/astermesh_view.html` | Custom path for the HTML map |
| `--no-html` | off | Skip HTML generation |

Generated files (default layout):

```text
<PATH>/xml-graph/
├── context_graph.xml     # compact Semantic XML for LLM context
├── GRAPH_REPORT.md       # macro architecture summary
└── astermesh_view.html   # interactive 2D map for humans
```

Examples:

```bash
astermesh build .
astermesh build /path/to/repo --no-html
astermesh build . -o artifacts/graph.xml
```

> The HTML map loads `vis-network` from a CDN, so it needs internet access to render.

### 3.2 `astermesh query`

Look up symbols or inspect the graph without starting a server.

```bash
astermesh query [SYMBOL] [-p PATH] [--overview] [--list-communities]
```

| Option | Description |
| :--- | :--- |
| `SYMBOL` | Case-insensitive. Matches any node whose ID contains `SYMBOL`, or whose short name equals `SYMBOL` |
| `-p`, `--path` | Workspace root (default `.`) |
| `--overview` | Print node, edge and community counts |
| `--list-communities` | Print every community with its God Node and size |

Output format for symbol lookup:

```text
[class] src.astermesh.graph.builder.GraphBuilder -> src/astermesh/graph/builder.py:24-259
```

Line numbers are 1-based and inclusive.

### 3.3 `astermesh serve`

Start the MCP server. The graph is built once at startup and then kept in sync by a file watcher.

```bash
astermesh serve [PATH] [--transport stdio|sse|streamable-http] [--host HOST] [--port PORT] [--no-watch]
```

| Option | Default | Description |
| :--- | :--- | :--- |
| `PATH` | `.` | Workspace root |
| `--transport` | `stdio` | MCP transport |
| `--host`, `--port` | `127.0.0.1`, `8000` | Bind address, used by the `sse` transport |
| `--no-watch` | off | Disable incremental updates |

### 3.4 `astermesh benchmark`

Compare the token size of the raw source code against the generated Semantic XML.

```bash
astermesh benchmark [PATH] [-r REPORT_PATH]
```

Tokens are counted with `tiktoken` (`cl100k_base`); if it is unavailable, the count falls back to `characters / 4`. Cost estimates assume USD 3.00 per 1M input tokens. See [BENCHMARK.md](../BENCHMARK.md) for methodology.

---

## 4. Using Astermesh with an AI Assistant (MCP)

### 4.1 Client configuration

Add the server to your client's `mcpServers` configuration (Cursor, Claude Desktop, Antigravity, ...):

```json
{
  "mcpServers": {
    "astermesh": {
      "command": "astermesh",
      "args": ["serve", "/absolute/path/to/your/project", "--transport", "stdio"]
    }
  }
}
```

If `astermesh` is not on the client's `PATH`, point `command` to the virtual environment binary, for example `/path/to/astermesh/.venv/bin/astermesh`.

### 4.2 Tools

| Tool | Arguments | Returns |
| :--- | :--- | :--- |
| `locate_symbol` | `name` | Up to 25 matching nodes with `file_path`, `line_start`, `line_end` |
| `get_file_outline` | `file_path` | All entities of a file, sorted by line |
| `get_neighbors` | `symbol`, `direction` = `in` / `out` / `both` | 1-hop incoming and outgoing relations with locations |
| `get_impact_radius` | `symbol`, `max_depth` = 2 | Transitive incoming dependents per depth, affected files, and a risk level (`LOW` < 3, `MEDIUM` < 10, `HIGH` >= 10 dependents) |
| `trace_path` | `source`, `target` (exact node IDs) | Shortest path between two nodes, ignoring edge direction |
| `get_community` | `name` (community name or ID) | All members of a Leiden community |
| `rebuild_graph` | none | Full rescan; returns node, edge and community counts |

`locate_symbol`, `get_neighbors` and `get_impact_radius` accept either a short name or a full node ID. `trace_path` requires exact IDs; obtain them with `locate_symbol` first.

### 4.3 Resources

| URI | Content |
| :--- | :--- |
| `codebase://report` | Macro architecture report (same content as `GRAPH_REPORT.md`) |
| `codebase://communities` | List of communities with God Nodes and sizes |

### 4.4 Recommended navigation protocol

To keep the assistant's context small, instruct it to follow a graph-first workflow. A ready-to-use rule file is provided at [`.astermesh/AGENTS.md`](../.astermesh/AGENTS.md):

1. **Orient**: read `codebase://report` or `GRAPH_REPORT.md`, then `get_community` for the relevant subsystem.
2. **Trace**: use `get_neighbors`, `get_impact_radius` and `trace_path` to understand dependencies before editing.
3. **Slice**: use `locate_symbol` and read only the returned `[line_start, line_end]` range.

---

## 5. Reading the Semantic XML

`context_graph.xml` uses a dictionary-encoded format to minimize tokens:

```xml
<c_graph root="myproject">
  <files>
    <f id="f1" p="core/database.py"/>
    <f id="f2" p="core/auth.py"/>
  </files>
  <c id="C1" name="Authenticator" size="4">
    <e id="Authenticator" t="c" f="f2" l="3-11"/>
    <e id="login" t="f" f="f2" l="4-8" c="f1:DBConnection,f1:connect" ref="f1:DB_TIMEOUT"/>
  </c>
</c_graph>
```

| Element / attribute | Meaning |
| :--- | :--- |
| `<f id p>` | File dictionary entry |
| `<c id name size>` | Community |
| `<e>` | Entity inside a community |
| `t` | Type: `c` class, `f` function, `v` variable |
| `f` | File ID from the dictionary |
| `l` | Line range `start-end` |
| `c`, `inh`, `ref` | Calls, inherits, references. Targets in another file are written as `fileId:Name` |

---

## 6. Python API

The package can be used as a library:

```python
from pathlib import Path

from astermesh import GraphBuilder
from astermesh.graph.clustering import CommunityClusterer
from astermesh.serializers.xml_context import export_semantic_xml

builder = GraphBuilder(Path("."))
graph = builder.build_full()                 # networkx.MultiDiGraph
communities = CommunityClusterer().cluster(graph)

node = graph.nodes["src.astermesh.graph.builder.GraphBuilder"]
print(node["file_path"], node["line_start"], node["line_end"])

export_semantic_xml(graph, communities, "astermesh", Path("out/context_graph.xml"))
```

Incremental updates:

```python
builder.update_file(Path("src/astermesh/cli.py"))   # re-parse one file
builder.remove_path("src/astermesh/old_module.py")   # drop a file and its entities
```

All graph mutations are guarded by `builder.lock` (a re-entrant lock); hold it when reading the graph from another thread.

---

## 7. Limitations

- Resolution is static: dynamic dispatch, `getattr`, monkey patching and runtime imports are not tracked.
- JavaScript / TypeScript support is limited to top-level declarations; exported declarations (`export function ...`), class methods and imports are not yet extracted.
- `trace_path` treats the graph as undirected, so a path indicates structural proximity, not necessarily an execution order.
- The HTML map is intended for small and medium repositories; very large graphs render slowly in the browser.

---

## 8. Troubleshooting

| Symptom | Cause / fix |
| :--- | :--- |
| `astermesh: command not found` | Activate the virtual environment, or run `python -m astermesh` |
| Symbol not found | Check that the file extension is supported and the directory is not ignored (Section 2.2). Remember FQNs depend on the indexed root (Section 2.3) |
| MCP client shows no tools | Use an absolute workspace path in `args`, and an absolute `command` path if the client does not inherit your shell `PATH` |
| HTML map is blank | The page needs internet access to load `vis-network` from the CDN |
| Graph is stale while serving | The watcher is disabled (`--no-watch`) or the file is in an ignored directory; call the `rebuild_graph` tool |
