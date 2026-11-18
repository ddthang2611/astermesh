---
name: astermesh-code-graph
description: >-
  Deterministic code knowledge graph navigation, blast-radius analysis, and
  precision AST line slicing via the Astermesh MCP server. Use when exploring
  unfamiliar codebases, tracing call graphs, analyzing transitive impact before
  refactoring, detecting architectural bottlenecks, or locating exact symbol
  definitions without broad text searches.
license: MIT
compatibility: >-
  Requires Python 3.10+ and the astermesh package installed locally and
  configured as an MCP server over stdio or streamable-http.
metadata:
  author: ddthang2611
  version: "1.3.3"
  repository: "https://github.com/ddthang2611/astermesh"
---

# Astermesh Graph-First Code Intelligence Protocol

This skill provides deterministic code intelligence for AI coding agents using the **Astermesh** MCP server.

---

## 1. Core Operating Principles

1. **Graph Before Grep**: Never start code exploration with broad text or regex searches for `.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.vue`, or `.svelte` source files. Query the in-memory AST graph first without waiting for user reminders.
2. **Line-Bounded Reading**: Never load an entire source file when only a specific class, function, or method is required. Resolve exact 1-based `[line_start, line_end]` boundaries via `locate_symbol` or `get_file_outline`, then read only that slice via `read_file_snippet`.
3. **Mandatory Blast-Radius Analysis**: Before altering any function signature, class contract, or public variable, compute all transitive callers via `get_impact_radius`.

---

## 2. MCP Tool Reference

| Task Objective | MCP Tool / Resource | Arguments | Output |
| :--- | :--- | :--- | :--- |
| Dense `#ACN2` repo graph | Resource `codebase://context` | None | Line-compressed `#ACN2` (L0/L1/L2) wire graph |
| Macro architecture summary | Resource `codebase://report` | None | Element counts, Leiden communities, God nodes |
| Module list | Resource `codebase://communities` | None | Community IDs, names, God nodes, sizes |
| Subsystem `#ACN2` zoom-in | `get_community_wire` | `name: str`, `max_tokens: int \| None = None` | Scoped `#ACN2` wire subgraph for single community |
| Subsystem entities | `get_community` | `name: str` | Member FQNs, file paths, line ranges |
| File structural outline | `get_file_outline` | `file_path: str` | Classes, functions, methods, variables, headings |
| Locate symbol & physical lines | `locate_symbol` | `name: str` | Canonical FQN, file path, line_start, line_end |
| Read bounded line slice | `read_file_snippet` | `file_path: str`, `start_line: int = 1`, `end_line: int \| None = None` | Clamped line-numbered source snippet |
| 1-Hop callers or callees | `get_neighbors` | `symbol: str`, `direction: "in" \| "out" \| "both"` | Direct incoming callers and outgoing dependencies |
| Transitive blast radius | `get_impact_radius` | `symbol: str`, `max_depth: int = 2` | Multi-hop dependents grouped by hop distance |
| Execution pathway tracing | `trace_path` | `source: str`, `target: str` | Shortest call/dependency chain connecting source to target |
| Full graph re-indexing | `rebuild_graph` | None | Refreshed node, edge, and community counts |

---

## 3. Standard Workflows

### Workflow 1: Unfamiliar Codebase Onboarding
1. Read resource `codebase://report` (or `codebase://context` for `#ACN2` wire view) to identify top central hubs and Leiden communities.
2. Zoom into the subsystem relevant to the task via `get_community_wire(name="<subsystem>")` or `get_community(name="<subsystem>")`.
3. Inspect central files with `get_file_outline(file_path="<path>")` to map logical structure before opening implementations.

### Workflow 2: Bug Localization & Call Tracing
1. Call `locate_symbol(name="<target_symbol>")` to resolve canonical FQN, file path, and `[line_start, line_end]`.
2. Call `read_file_snippet(file_path="<path>", start_line=..., end_line=...)` to read only the specified line boundaries.
3. If investigating caller-callee interactions, call `trace_path(source="<caller>", target="<callee>")` to verify intermediate hops.
4. Call `get_neighbors(symbol="<target_symbol>", direction="out")` to review dependencies invoked by the target.

### Workflow 3: Safe Multi-File Refactoring
1. Resolve the canonical target symbol via `locate_symbol(name="<symbol>")`.
2. Compute all affected call sites via `get_impact_radius(symbol="<canonical_fqn>", max_depth=3)`.
3. Read the exact line bounds for each affected caller via `read_file_snippet` to determine necessary call-site migrations.
4. Apply the modifications to the target and all affected callers. The file watcher updates the graph in real-time.
5. Verify that all call edges remain intact via `get_neighbors(symbol="<canonical_fqn>", direction="in")`.

### Workflow 4: Architectural Health & Modularity Audit
1. Read resource `codebase://report` to locate high-degree God nodes.
2. Call `get_neighbors(symbol="<god_node_fqn>", direction="both")` to separate internal module operations from cross-community dependencies.
3. Propose modular decoupling that reduces cross-community edges while maintaining cluster cohesion.
