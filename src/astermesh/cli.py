from pathlib import Path
from typing import Optional
import typer
from astermesh.graph.builder import GraphBuilder
from astermesh.graph.clustering import CommunityClusterer
from astermesh.serializers.html_visual import export_html_visual
from astermesh.serializers.xml_context import export_semantic_xml
from astermesh.server import create_mcp_server

app = typer.Typer(help="Astermesh: Deterministic Code Knowledge Graph & MCP Server")


# lenh build snapshot thu cong
@app.command()
def build(
    path: Path = typer.Argument(Path("."), help="Workspace root directory"),
    output: Path = typer.Option(
        Path("context_graph.xml"), "-o", "--output", help="Semantic XML output path"
    ),
    html: Optional[Path] = typer.Option(
        None, "--html", help="Optional 2D HTML visual output path"
    ),
) -> None:
    builder = GraphBuilder(path)
    graph = builder.build_full()
    clusterer = CommunityClusterer()
    communities = clusterer.cluster(graph)

    export_semantic_xml(graph, communities, path.resolve().name, output)
    typer.echo(
        f"Built graph: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges, "
        f"{len(communities)} communities -> {output}"
    )

    if html:
        export_html_visual(graph, html)
        typer.echo(f"Exported 2D visualization -> {html}")


# lenh chay mcp daemon server kem file watcher
@app.command()
def serve(
    path: Path = typer.Argument(Path("."), help="Workspace root directory"),
    transport: str = typer.Option(
        "stdio", "--transport", help="MCP transport: stdio, sse, streamable-http"
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="Host binding for SSE transport"),
    port: int = typer.Option(8000, "--port", help="Port for SSE transport"),
    no_watch: bool = typer.Option(
        False, "--no-watch", help="Disable incremental file watcher"
    ),
) -> None:
    mcp = create_mcp_server(path, watch=not no_watch)
    if transport == "sse":
        mcp.run(transport="sse", host=host, port=port)
    else:
        mcp.run(transport=transport)


# lenh tra cuu nhanh symbol tu cli
@app.command()
def query(
    symbol: str = typer.Argument(..., help="Symbol name or FQN to locate"),
    path: Path = typer.Option(Path("."), "-p", "--path", help="Workspace root directory"),
) -> None:
    builder = GraphBuilder(path)
    graph = builder.build_full()
    q = symbol.lower()
    found = False
    for nid, data in graph.nodes(data=True):
        if q in nid.lower() or q == data.get("name", "").lower():
            found = True
            typer.echo(
                f"[{data.get('type')}] {nid} -> "
                f"{data.get('file_path')}:{data.get('line_start')}-{data.get('line_end')}"
            )
    if not found:
        typer.echo(f"Symbol '{symbol}' not found in codebase.")


if __name__ == "__main__":
    app()
