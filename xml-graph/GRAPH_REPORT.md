# Architecture Snapshot: `astermesh`

## 1. Macro Statistics
- Total Elements: 404 nodes, 850 relations
- Breakdown: 21 Classes, 146 Functions, 0 Components, 0 Services, 0 Hooks, 0 Assets, 45 Files

## 2. Functional Communities (Leiden Clusters)
| ID | Community Name | Central God Node | Size |
| :--- | :--- | :--- | :--- |
| C1 | `ASTEngine` | `src.astermesh.parser.engine.ASTEngine` | 45 entities |
| C2 | `GraphQuery` | `src.astermesh.graph.query.GraphQuery` | 33 entities |
| C3 | `GraphBuilder` | `src.astermesh.graph.builder.GraphBuilder` | 27 entities |
| C4 | `ModuleResolver` | `src.astermesh.parser.resolver.ModuleResolver` | 23 entities |
| C5 | `SatelliteParser` | `src.astermesh.parser.satellite.SatelliteParser` | 20 entities |
| C6 | `TokenBenchmarkRunner` | `src.astermesh.benchmark.runner.TokenBenchmarkRunner` | 15 entities |
| C7 | `GraphEventHandler` | `src.astermesh.graph.watcher.GraphEventHandler` | 13 entities |
| C8 | `test_render_and_export_semantic_xml` | `tests.test_serializers.test_render_and_export_semantic_xml` | 11 entities |

## 3. Top Core Hubs
- `src.astermesh.parser.engine.ASTEngine` (class, degree=36) -> `src/astermesh/parser/engine.py`
- `src.astermesh.graph.builder.GraphBuilder` (class, degree=27) -> `src/astermesh/graph/builder.py`
- `src.astermesh.parser.resolver.ModuleResolver.resolve` (function, degree=19) -> `src/astermesh/parser/resolver.py`
- `src.astermesh.graph.builder.GraphBuilder.build_full` (function, degree=18) -> `src/astermesh/graph/builder.py`
- `src.astermesh.server.create_mcp_server` (function, degree=18) -> `src/astermesh/server.py`
