# Astermesh Graph-First AI Navigation Rules

When inspecting, modifying, or refactoring code in this repository, AI Coding Assistants MUST follow this 3-step navigation protocol to minimize token overhead and prevent hallucinations.

---

## The 3-Step Navigation Protocol

### Step 1: Macro Orientation (Architecture Overview)
- Read `xml-graph/GRAPH_REPORT.md` or call MCP tool `get_community(name)` to identify which functional subsystem the task belongs to.
- Do NOT perform blind global searches (`grep_search` or `find_by_name`).

### Step 2: Micro Tracing (Dependency & Impact Analysis)
- Call MCP tool `get_neighbors(symbol, direction="both")` to inspect immediate 1-hop callers (`in`) and callees (`out`).
- Call MCP tool `trace_path(source, target)` to trace execution paths across decoupled modules.

### Step 3: Precision Slicing (Physical Code Reading)
- Call MCP tool `locate_symbol(name)` to obtain exact 1-based physical line boundaries `[line_start, line_end]`.
- Read ONLY the exact line range using line-bounded file reading tools. Do NOT load full 500+ line files into working memory.
