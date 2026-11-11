from pathlib import Path

import networkx as nx

from astermesh.graph.model import CommunityInfo, EdgeType, NodeType
from astermesh.serializers.compact_wire import export_compact_wire
from astermesh.serializers.html_visual import export_html_visual
from astermesh.serializers.markdown_report import export_markdown_report, render_markdown_report
from astermesh.serializers.xml_context import export_semantic_xml, render_semantic_xml


def _build_sample_graph():
    g = nx.MultiDiGraph()
    g.add_node("file_a.py", type=NodeType.FILE.value, file_path="file_a.py")
    g.add_node("file_b.py", type=NodeType.FILE.value, file_path="file_b.py")
    g.add_node("file_a.Hero", type=NodeType.CLASS.value, name="Hero", file_path="file_a.py", line_start=1, line_end=20)
    g.add_node("file_b.villain", type=NodeType.FUNCTION.value, name="villain", file_path="file_b.py", line_start=1, line_end=10)

    g.add_edge("file_a.Hero", "file_b.villain", type=EdgeType.CALLS.value)
    g.add_edge("file_a.py", "file_b.villain", type=EdgeType.IMPORTS.value)
    return g


def test_render_and_export_semantic_xml(tmp_path: Path):
    # arrange
    g = _build_sample_graph()
    comms = [CommunityInfo(id=0, name="battle", size=2, god_node="file_a.Hero", members=["file_a.Hero", "file_b.villain"])]

    # act: render in-memory
    xml_str = render_semantic_xml(g, comms, "my_repo", compact=True)

    # assert
    assert "<c_graph" in xml_str
    assert "Astermesh Compact Knowledge Graph" in xml_str
    assert "<files>" in xml_str
    assert "file_a.py" in xml_str

    # act: export to file
    out_file = tmp_path / "graph.xml"
    export_semantic_xml(g, comms, "my_repo", out_file, compact=True)
    assert out_file.exists()
    assert out_file.read_text(encoding="utf-8") == xml_str


def test_render_and_export_markdown_report(tmp_path: Path):
    # arrange
    g = _build_sample_graph()
    comms = [CommunityInfo(id=0, name="battle", size=2, god_node="file_a.Hero", members=["file_a.Hero", "file_b.villain"])]

    # act: render in-memory
    report_str = render_markdown_report(g, comms, "my_repo")

    # assert
    assert "# Architecture Snapshot: `my_repo`" in report_str
    assert "## 1. Macro Statistics" in report_str
    assert "## 2. Functional Communities" in report_str

    # act: export to file
    out_file = tmp_path / "report.md"
    export_markdown_report(g, comms, "my_repo", out_file)
    assert out_file.exists()
    assert out_file.read_text(encoding="utf-8") == report_str


# kiem thu verbose xml, fe compact xml, acn wire va html visual
def test_serializers_fe_modes_and_html_visual(tmp_path: Path):
    g = nx.MultiDiGraph()
    g.add_node("src/App.tsx", type=NodeType.FILE.value, file_path="src/App.tsx")
    g.add_node(
        "src/assets/icon.svg",
        type=NodeType.ASSET.value,
        name="icon.svg",
        file_path="src/assets/icon.svg",
    )
    g.add_node(
        "src.App.App",
        type=NodeType.COMPONENT.value,
        name="App",
        file_path="src/App.tsx",
        line_start=1,
        line_end=10,
    )
    g.add_node(
        "src.App.useAuth",
        type=NodeType.HOOK.value,
        name="useAuth",
        file_path="src/App.tsx",
        line_start=12,
        line_end=15,
    )
    g.add_edge("src.App.App", "src.App.useAuth", type=EdgeType.CALLS.value)
    g.add_edge("src.App.App", "src/assets/icon.svg", type=EdgeType.RENDERS.value)

    comms = [
        CommunityInfo(
            id="C1",
            name="App",
            size=2,
            god_node="src.App.App",
            members=["src.App.App", "src.App.useAuth"],
        )
    ]

    verbose_xml = render_semantic_xml(g, comms, "fe_app", compact=False)
    assert "<codebase_knowledge_graph" in verbose_xml
    assert "<renders target=" in verbose_xml

    compact_xml = render_semantic_xml(g, comms, "fe_app", compact=True)
    assert 't="u"' in compact_xml
    assert 't="k"' in compact_xml
    assert 'ui="src/assets/icon.svg"' in compact_xml

    acn_path = tmp_path / "graph.acn"
    acn_text = export_compact_wire(g, comms, "fe_app", acn_path)
    assert acn_path.exists()
    assert "~icon.svg" in acn_text
    assert ">useAuth" in acn_text

    html_path = tmp_path / "view.html"
    export_html_visual(g, html_path)
    assert html_path.exists()
    html_content = html_path.read_text(encoding="utf-8")
    assert "src.App.App" in html_content
    assert "#ec4899" in html_content
