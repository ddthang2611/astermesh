from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from astermesh.graph.builder import GraphBuilder
from astermesh.graph.clustering import CommunityClusterer
from astermesh.graph.query import GraphQuery
from astermesh.serializers.compact_wire import export_compact_wire
from astermesh.serializers.html_visual import export_html_visual
from astermesh.serializers.markdown_report import export_markdown_report
from astermesh.serializers.xml_context import export_semantic_xml
from astermesh.server import create_mcp_server

DEFAULT_OUTPUT_DIR = "xml-graph"


class TransportType(str, Enum):
    stdio = "stdio"
    sse = "sse"
    streamable_http = "streamable-http"


class OutputFormat(str, Enum):
    acn = "acn"
    xml = "xml"
    all = "all"


class LeidenMode(str, Enum):
    modularity = "modularity"
    cpm = "cpm"


app = typer.Typer(help="Astermesh: Deterministic Code Knowledge Graph & MCP Server")


# build snapshot command
@app.command()
def build(
    path: Annotated[Path, typer.Argument(help="Workspace root directory")] = Path("."),
    out_dir: Annotated[
        Path,
        typer.Option(
            "-o",
            "--output",
            "--output-dir",
            help="Output directory or specific graph artifact path",
        ),
    ] = Path(DEFAULT_OUTPUT_DIR),
    fmt: Annotated[
        OutputFormat,
        typer.Option("--format", help="Context format: acn (dense wire), xml, or all"),
    ] = OutputFormat.acn,
    leiden_mode: Annotated[
        LeidenMode,
        typer.Option("--leiden-mode", help="Leiden objective: modularity or cpm"),
    ] = LeidenMode.modularity,
    html: Annotated[
        Path | None, typer.Option("--html", help="Optional specific HTML visual path")
    ] = None,
    no_html: Annotated[
        bool, typer.Option("--no-html", help="Disable generating human HTML visual")
    ] = False,
) -> None:
    resolved_path = path.resolve()
    ext = out_dir.suffix.lower()
    if ext == ".xml":
        xml_path = out_dir if out_dir.is_absolute() else (resolved_path / out_dir)
        target_dir = xml_path.parent
        acn_path = target_dir / "context_graph.acn"
        fmt = OutputFormat.xml
    elif ext == ".acn":
        acn_path = out_dir if out_dir.is_absolute() else (resolved_path / out_dir)
        target_dir = acn_path.parent
        xml_path = target_dir / "context_graph.xml"
        fmt = OutputFormat.acn
    else:
        target_dir = out_dir if out_dir.is_absolute() else (resolved_path / out_dir)
        acn_path = target_dir / "context_graph.acn"
        xml_path = target_dir / "context_graph.xml"

    target_dir.mkdir(parents=True, exist_ok=True)
    html_path = html if html else (target_dir / "astermesh_view.html")
    report_path = target_dir / "GRAPH_REPORT.md"

    builder = GraphBuilder(path)
    graph = builder.build_full()
    clusterer = CommunityClusterer(partition_mode=leiden_mode.value)
    communities = clusterer.cluster(graph)

    exported_targets: list[Path] = []
    if fmt in (OutputFormat.acn, OutputFormat.all):
        export_compact_wire(graph, communities, resolved_path.name, acn_path)
        exported_targets.append(acn_path)
    if fmt in (OutputFormat.xml, OutputFormat.all):
        export_semantic_xml(graph, communities, resolved_path.name, xml_path)
        exported_targets.append(xml_path)

    export_markdown_report(graph, communities, resolved_path.name, report_path)
    targets_str = ", ".join(str(p) for p in exported_targets)
    typer.echo(
        f"Built graph: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges, "
        f"{len(communities)} communities -> {targets_str}"
    )
    typer.echo(f"Exported architecture summary report -> {report_path}")

    if not no_html:
        export_html_visual(graph, html_path)
        typer.echo(f"Exported 2D visualization for human -> {html_path}")


# run mcp daemon server command
@app.command()
def serve(
    path: Annotated[Path, typer.Argument(help="Workspace root directory")] = Path("."),
    transport: Annotated[
        TransportType,
        typer.Option("--transport", help="MCP transport: stdio, sse, streamable-http"),
    ] = TransportType.stdio,
    host: Annotated[
        str, typer.Option("--host", help="Host binding for SSE transport")
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Port for SSE transport")] = 8000,
    no_watch: Annotated[
        bool, typer.Option("--no-watch", help="Disable incremental file watcher")
    ] = False,
) -> None:
    mcp = create_mcp_server(path, watch=not no_watch)
    if transport == TransportType.sse:
        mcp.run(transport="sse", host=host, port=port)
    else:
        mcp.run(transport=transport.value)


# query symbol command
@app.command()
def query(
    symbol: Annotated[
        str | None, typer.Argument(help="Symbol name, suffix, or FQN to locate")
    ] = None,
    path: Annotated[
        Path, typer.Option("-p", "--path", help="Workspace root directory")
    ] = Path("."),
    overview: Annotated[
        bool, typer.Option("--overview", help="Display macro graph statistics")
    ] = False,
    list_communities: Annotated[
        bool, typer.Option("--list-communities", help="List detected functional communities")
    ] = False,
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
    path: Annotated[Path, typer.Argument(help="Target repository directory to benchmark")] = Path("."),
    report: Annotated[
        Path | None,
        typer.Option("-r", "--report", help="Optional markdown report output path"),
    ] = None,
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


if __name__ == "__main__":
    app()
