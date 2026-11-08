# Architectural Knowledge Graph Report: astermesh

## 1. Executive Summary (Working Memory Digest)
- **Total Nodes**: 129 (Files: 27, Classes: 11, Functions: 68, Variables: 9)
- **Total Edges**: 321
- **Architectural Communities**: 16

## 2. Macro Topology & Community Breakdown
| ID | Community Name | God Node (Anchor Entity) | Size |
|---|---|---|---|
| C1 | `ASTEngine` | `src.astermesh.parser.engine.ASTEngine` | 22 |
| C2 | `GraphBuilder` | `src.astermesh.graph.builder.GraphBuilder` | 15 |
| C3 | `create_mcp_server` | `src.astermesh.server.create_mcp_server` | 14 |
| C4 | `cluster` | `src.astermesh.graph.clustering.CommunityClusterer.cluster` | 13 |
| C5 | `_scan_filesystem` | `src.astermesh.graph.builder.GraphBuilder._scan_filesystem` | 7 |
| C6 | `_setup_sandbox_repository` | `tests.test_e2e_sandbox._setup_sandbox_repository` | 7 |
| C7 | `neighbourhoodHighlight` | `lib.bindings.utils.neighbourhoodHighlight` | 1 |
| C8 | `filterHighlight` | `lib.bindings.utils.filterHighlight` | 1 |
| C9 | `selectNode` | `lib.bindings.utils.selectNode` | 1 |
| C10 | `selectNodes` | `lib.bindings.utils.selectNodes` | 1 |
| C11 | `highlightFilter` | `lib.bindings.utils.highlightFilter` | 1 |
| C12 | `__version__` | `src.astermesh.__version__` | 1 |
| C13 | `EdgeType` | `src.astermesh.graph.model.EdgeType` | 1 |
| C14 | `__all__` | `src.astermesh.graph.__all__` | 1 |
| C15 | `__all__` | `src.astermesh.parser.__all__` | 1 |
| C16 | `__all__` | `src.astermesh.serializers.__all__` | 1 |

## 3. High-Centrality Hub Entities (Top God Nodes)
| Entity (FQN) | Type | Total Connections (Degree) | File Location |
|---|---|---|---|
| `src.astermesh.graph.builder.GraphBuilder` | `class` | 29 | `src/astermesh/graph/builder.py:24-259` |
| `src.astermesh.parser.engine.ASTEngine` | `class` | 24 | `src/astermesh/parser/engine.py:37-341` |
| `src.astermesh.server.create_mcp_server` | `function` | 15 | `src/astermesh/server.py:12-203` |
| `src.astermesh.graph.builder.GraphBuilder.build_full` | `function` | 13 | `src/astermesh/graph/builder.py:36-47` |
| `src.astermesh.graph.clustering.CommunityClusterer` | `class` | 13 | `src/astermesh/graph/clustering.py:11-56` |
| `src.astermesh.graph.builder.GraphBuilder.update_file` | `function` | 10 | `src/astermesh/graph/builder.py:211-219` |
| `src.astermesh.graph.clustering.CommunityClusterer.cluster` | `function` | 9 | `src/astermesh/graph/clustering.py:12-56` |
| `src.astermesh.graph.model.NodeType` | `class` | 9 | `src/astermesh/graph/model.py:7-13` |

## 5. LLM Graph-First Navigation Protocol
1. **Never read raw files or run text grep blindly**: Locate the relevant community or symbol first.
2. **Query `locate_symbol(name)`**: Retrieve the exact physical line range `[line_start, line_end]` to load only 20-50 lines into context RAM.
3. **Query `get_neighbors(symbol)`**: Inspect incoming callers and outgoing dependencies in ~50 tokens instead of reading surrounding files.
4. **Query `trace_path(source, target)`**: Verify call paths across modules deterministically without hallucination.
