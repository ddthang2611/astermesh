# Architecture Snapshot: `astermesh`

## 1. Macro Statistics
- Total Elements: 164 nodes, 411 relations
- Breakdown: 15 Classes, 90 Functions, 12 Variables, 31 Files

## 2. Functional Communities (Leiden Clusters)
| ID | Community Name | Central God Node | Size |
| :--- | :--- | :--- | :--- |
| C1 | `ASTEngine` | `src.astermesh.parser.engine.ASTEngine` | 24 entities |
| C2 | `GraphBuilder` | `src.astermesh.graph.builder.GraphBuilder` | 21 entities |
| C3 | `create_mcp_server` | `src.astermesh.server.create_mcp_server` | 21 entities |
| C4 | `build` | `src.astermesh.cli.build` | 19 entities |
| C5 | `run` | `src.astermesh.benchmark.runner.TokenBenchmarkRunner.run` | 17 entities |
| C6 | `neighbourhoodHighlight` | `lib.bindings.utils.neighbourhoodHighlight` | 1 entities |
| C7 | `filterHighlight` | `lib.bindings.utils.filterHighlight` | 1 entities |
| C8 | `selectNode` | `lib.bindings.utils.selectNode` | 1 entities |

## 3. Top Core Hubs
- `src.astermesh.parser.engine.ASTEngine` (class, degree=21) -> `src/astermesh/parser/engine.py`
- `src.astermesh.graph.builder.GraphBuilder` (class, degree=17) -> `src/astermesh/graph/builder.py`
- `src.astermesh.server.create_mcp_server` (function, degree=15) -> `src/astermesh/server.py`
- `src.astermesh.graph.query.GraphQuery` (class, degree=15) -> `src/astermesh/graph/query.py`
- `src.astermesh.benchmark.runner.TokenBenchmarkRunner.run` (function, degree=12) -> `src/astermesh/benchmark/runner.py`
