from enum import Enum
from pathlib import Path
import typer
from astermesh.graph.builder import GraphBuilder
from astermesh.graph.clustering import CommunityClusterer
from astermesh.graph.query import GraphQuery
from astermesh.serializers.html_visual import export_html_visual
from astermesh.serializers.markdown_report import export_markdown_report
from astermesh.serializers.xml_context import export_semantic_xml
from astermesh.server import create_mcp_server

DEFAULT_OUTPUT_DIR = "xml-graph"


class TransportType(str, Enum):
    stdio = "stdio"
    sse = "sse"
    streamable_http = "streamable-http"


app = typer.Typer(help="Astermesh: Deterministic Code Knowledge Graph & MCP Server")


# build snapshot command
@app.command()
def build(
    path: Path = typer.Argument(Path("."), help="Workspace root directory"),
    out_dir: Path = typer.Option(
        Path(DEFAULT_OUTPUT_DIR),
        "-o",
        "--output",
        "--output-dir",
        help="Output directory or specific XML path for artifacts",
    ),
    html: Path | None = typer.Option(
        None, "--html", help="Optional specific HTML visual path"
    ),
    no_html: bool = typer.Option(
        False, "--no-html", help="Disable generating human HTML visual"
    ),
) -> None:
    resolved_path = path.resolve()
    if out_dir.suffix.lower() == ".xml":
        xml_path = out_dir if out_dir.is_absolute() else (resolved_path / out_dir)
        target_dir = xml_path.parent
    else:
        target_dir = out_dir if out_dir.is_absolute() else (resolved_path / out_dir)
        xml_path = target_dir / "context_graph.xml"

    target_dir.mkdir(parents=True, exist_ok=True)
    html_path = html if html else (target_dir / "astermesh_view.html")
    report_path = target_dir / "GRAPH_REPORT.md"

    builder = GraphBuilder(path)
    graph = builder.build_full()
    clusterer = CommunityClusterer()
    communities = clusterer.cluster(graph)

    export_semantic_xml(graph, communities, resolved_path.name, xml_path)
    export_markdown_report(graph, communities, resolved_path.name, report_path)
    typer.echo(
        f"Built graph: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges, "
        f"{len(communities)} communities -> {xml_path}"
    )
    typer.echo(f"Exported architecture summary report -> {report_path}")

    if not no_html:
        export_html_visual(graph, html_path)
        typer.echo(f"Exported 2D visualization for human -> {html_path}")


# run mcp daemon server command
@app.command()
def serve(
    path: Path = typer.Argument(Path("."), help="Workspace root directory"),
    transport: TransportType = typer.Option(
        TransportType.stdio, "--transport", help="MCP transport: stdio, sse, streamable-http"
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="Host binding for SSE transport"),
    port: int = typer.Option(8000, "--port", help="Port for SSE transport"),
    no_watch: bool = typer.Option(
        False, "--no-watch", help="Disable incremental file watcher"
    ),
) -> None:
    mcp = create_mcp_server(path, watch=not no_watch)
    if transport == TransportType.sse:
        mcp.run(transport="sse", host=host, port=port)
    else:
        mcp.run(transport=transport.value)


# query symbol command
@app.command()
def query(
    symbol: str | None = typer.Argument(None, help="Symbol name or FQN to locate"),
    path: Path = typer.Option(Path("."), "-p", "--path", help="Workspace root directory"),
    overview: bool = typer.Option(False, "--overview", help="Display macro graph statistics"),
    list_communities: bool = typer.Option(
        False, "--list-communities", help="List detected functional communities"
    ),
) -> None:
    builder = GraphBuilder(path)
    graph = builder.build_full()

    if overview or list_communities:
        clusterer = CommunityClusterer()
        communities = clusterer.cluster(graph)

        if overview:
            total_nodes = graph.number_of_nodes()
            total_edges = graph.number_of_edges()
            typer.echo(f"Repository Overview: {path.resolve().name}")
            typer.echo(f"- Total Nodes: {total_nodes}")
            typer.echo(f"- Total Edges: {total_edges}")
            typer.echo(f"- Total Communities: {len(communities)}")
            return

        if list_communities:
            typer.echo(f"Discovered Communities for: {path.resolve().name}")
            for c in communities:
                typer.echo(f"- [{c.id}] {c.name} (God Node: {c.god_node}, Size: {c.size})")
            return

    if not symbol:
        typer.echo("Please provide a symbol to locate, or use --overview / --list-communities.")
        return

    query_engine = GraphQuery(graph)
    results = query_engine.locate(symbol)
    if not results:
        typer.echo(f"Symbol '{symbol}' not found in codebase.")
        return

    for data in results:
        typer.echo(
            f"[{data.get('type')}] {data.get('id')} -> "
            f"{data.get('file_path')}:{data.get('line_start')}-{data.get('line_end')}"
        )


# token benchmark command
@app.command()
def benchmark(
    path: Path = typer.Argument(Path("."), help="Target repository directory to benchmark"),
    report: Path | None = typer.Option(
        None, "-r", "--report", help="Optional markdown report output path"
    ),
) -> None:
    from astermesh.benchmark.runner import TokenBenchmarkRunner

    typer.echo(f"Running token benchmark on: {path.resolve()} ...")
    runner = TokenBenchmarkRunner(path)
    metrics = runner.run()

    report_md = runner.format_markdown_report(metrics)
    typer.echo(report_md)

    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(report_md, encoding="utf-8")
        typer.echo(f"Saved benchmark report to: {report}")

