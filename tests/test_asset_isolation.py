from pathlib import Path

from astermesh.graph.builder import GraphBuilder
from astermesh.graph.clustering import CommunityClusterer
from astermesh.graph.model import EdgeType
from astermesh.graph.query import GraphQuery
from astermesh.serializers.compact_wire import render_compact_wire
from astermesh.serializers.xml_context import render_semantic_xml


def test_five_invariants_of_isolated_asset_space(tmp_path: Path):
    # arrange
    (tmp_path / "src/assets").mkdir(parents=True)
    (tmp_path / "src/assets/logo.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "src/assets/unused.png").write_text("PNG", encoding="utf-8")
    (tmp_path / "src/btn.module.css").write_text(".btn {}", encoding="utf-8")
    (tmp_path / "src/global.css").write_text("body {}", encoding="utf-8")

    (tmp_path / "src/PrimaryButton.tsx").write_text(
        """import LogoIcon from './assets/logo.svg?react';
import styles from './btn.module.css';
import './global.css';

export function PrimaryButton() {
    return <button className={styles.btn}><LogoIcon /></button>;
}

export function SecondaryText() {
    return <span>ok</span>;
}
""",
        encoding="utf-8",
    )

    builder = GraphBuilder(tmp_path)
    g = builder.build_full()

    # invariant 5: lazy asset creation (unused.png must NOT exist in graph)
    assert g.has_node("src/assets/logo.svg")
    assert g.has_node("src/btn.module.css")
    assert g.has_node("src/global.css")
    assert not g.has_node("src/assets/unused.png")

    # invariant 4: two-step function-level edge mapping
    btn_edges = [(v, d["type"]) for _, v, d in g.out_edges("src.PrimaryButton.PrimaryButton", data=True)]
    assert ("src/assets/logo.svg", EdgeType.RENDERS.value) in btn_edges
    assert ("src/btn.module.css", EdgeType.REFERENCES.value) in btn_edges

    # side-effect import stays at file level
    file_edges = [(v, d["type"]) for _, v, d in g.out_edges("src/PrimaryButton.tsx", data=True)]
    assert ("src/global.css", EdgeType.IMPORTS.value) in file_edges

    # reverse impact_radius from asset reaches consuming component
    q = GraphQuery(g)
    impact = q.impact_radius("src/assets/logo.svg", max_depth=2)
    affected_ids = [item["id"] for item in impact["impact_by_depth"].get(1, [])]
    assert "src.PrimaryButton.PrimaryButton" in affected_ids
    assert "src.PrimaryButton.SecondaryText" not in affected_ids

    # invariant 1: asset isolation from leiden clustering
    clusterer = CommunityClusterer(seed=0, partition_mode="cpm", resolution_parameter=0.05)
    communities = clusterer.cluster(g)
    for c in communities:
        assert "src/assets/logo.svg" not in c.members
        assert "src/btn.module.css" not in c.members
    assert g.nodes["src/assets/logo.svg"]["community_id"] is None

    # serializers exclude isolated asset files from file table
    xml_out = render_semantic_xml(g, communities, "app")
    assert "logo.svg" not in xml_out.split("<c ")[0]

    acn_out = render_compact_wire(g, communities, "app")
    assert acn_out.startswith("#ACN1 ")
    file_table_line = acn_out.splitlines()[1]
    assert file_table_line.startswith("@F ")
    assert "logo.svg" not in file_table_line
    assert "src/PrimaryButton.tsx" in file_table_line
