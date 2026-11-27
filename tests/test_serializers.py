from pathlib import Path

import networkx as nx
import pytest

from astermesh.graph.model import CommunityInfo, EdgeType, NodeType
from astermesh.serializers.compact_wire import export_compact_wire
from astermesh.serializers.html_visual import export_html_visual
from astermesh.serializers.markdown_report import export_markdown_report, render_markdown_report
from astermesh.serializers.xml_context import export_semantic_xml, render_semantic_xml


def _build_sample_graph():
    g = nx.MultiDiGraph()
    g.add_node("file_a.py", type=NodeType.FILE.value, file_path="file_a.py")
    g.add_node("file_b.py", type=NodeType.FILE.value, file_path="file_b.py")
    g.add_node(
        "file_a.Hero",
        type=NodeType.CLASS.value,
        name="Hero",
        file_path="file_a.py",
        line_start=1,
        line_end=20,
    )
    g.add_node(
        "file_b.villain",
        type=NodeType.FUNCTION.value,
        name="villain",
        file_path="file_b.py",
        line_start=1,
        line_end=10,
    )

    g.add_edge("file_a.Hero", "file_b.villain", type=EdgeType.CALLS.value)
    g.add_edge("file_a.py", "file_b.villain", type=EdgeType.IMPORTS.value)
    return g


# kiem thu canh bao ngung ho tro xml legacy
def test_render_and_export_semantic_xml(tmp_path: Path):
    g = _build_sample_graph()
    comms = [
        CommunityInfo(
            id="C0",
            name="battle",
            size=2,
            god_node="file_a.Hero",
            members=["file_a.Hero", "file_b.villain"],
        )
    ]

    with pytest.warns(DeprecationWarning):
        xml_str = render_semantic_xml(g, comms, "my_repo", compact=True)
    assert "<c_graph" in xml_str
    assert "<files>" in xml_str

    with pytest.warns(DeprecationWarning):
        verbose_xml = render_semantic_xml(g, comms, "my_repo", compact=False)
    assert "<codebase_knowledge_graph" in verbose_xml

    out_file = tmp_path / "graph.xml"
    with pytest.warns(DeprecationWarning):
        export_semantic_xml(g, comms, "my_repo", out_file, compact=True)
    assert out_file.read_text(encoding="utf-8") == xml_str


# kiem thu xuat bao cao kien truc markdown
def test_render_and_export_markdown_report(tmp_path: Path):
    g = _build_sample_graph()
    comms = [
        CommunityInfo(
            id="C0",
            name="battle",
            size=2,
            god_node="file_a.Hero",
            members=["file_a.Hero", "file_b.villain"],
        )
    ]

    report_str = render_markdown_report(g, comms, "my_repo")
    assert "# Architecture Snapshot: `my_repo`" in report_str
    assert "## 1. Macro Statistics" in report_str
    assert "## 2. Functional Communities" in report_str

    out_file = tmp_path / "report.md"
    export_markdown_report(g, comms, "my_repo", out_file)
    assert out_file.read_text(encoding="utf-8") == report_str


# kiem thu xuat acn2 wire fe va html visual
def test_serializers_acn2_and_html_visual(tmp_path: Path):
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

    acn_path = tmp_path / "graph.acn"
    acn_text = export_compact_wire(g, comms, "fe_app", acn_path)
    assert acn_path.exists()
    assert "#ACN2 " in acn_text
    assert "@A a1:src/assets/icon.svg" in acn_text
    assert "App*:u@1-10" in acn_text
    assert "~a1" in acn_text
    assert ">useAuth" in acn_text

    html_path = tmp_path / "view.html"
    export_html_visual(g, html_path)
    assert html_path.exists()
    html_content = html_path.read_text(encoding="utf-8")
    assert "src.App.App" in html_content
    assert "#ec4899" in html_content


# java satellite manifests and json bloat guards
def test_satellite_parser_java_manifests_and_json_bloat_guards(tmp_path: Path) -> None:
    from astermesh.parser.satellite import MAX_SATELLITE_ENTITIES, SatelliteParser

    # arrange
    parser = SatelliteParser()

    pom = tmp_path / "pom.xml"
    pom.write_text(
        """<project>
  <parent>
    <groupId>org.acme</groupId>
  </parent>
  <artifactId>billing-app</artifactId>
  <modules>
    <module>core</module>
  </modules>
  <dependencies>
    <dependency>
      <groupId>com.google.inject</groupId>
      <artifactId>guice</artifactId>
    </dependency>
  </dependencies>
</project>
""",
        encoding="utf-8",
    )
    pom_xxe = tmp_path / "bad" / "pom.xml"
    pom_xxe.parent.mkdir()
    pom_xxe.write_text(
        '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><project/>',
        encoding="utf-8",
    )

    gradle = tmp_path / "build.gradle"
    gradle.write_text(
        """plugins {
    id 'java'
    kotlin('jvm')
}
dependencies {
    implementation 'org.springframework.boot:spring-boot-starter-web:3.2.0'
    implementation project(':shared-api')
}
""",
        encoding="utf-8",
    )
    settings_kts = tmp_path / "settings.gradle.kts"
    settings_kts.write_text('include(":core", ":api")\n', encoding="utf-8")

    props = tmp_path / "application.properties"
    props.write_text(
        """# database settings
spring.datasource.url=jdbc:postgresql://localhost/db
multiline.query=SELECT * \\
  FROM orders
server.port=8080
""",
        encoding="utf-8",
    )

    sample_json = tmp_path / "sample-data" / "records.json"
    sample_json.parent.mkdir()
    sample_json.write_text('{\n  "id": 1\n}\n', encoding="utf-8")

    huge_json = tmp_path / "huge.json"
    huge_json.write_text('{\n  "k": "' + ("x" * 70_000) + '"\n}\n', encoding="utf-8")

    wide_json = tmp_path / "wide.json"
    wide_entries = ",\n".join(f'  "key_{i}": {i}' for i in range(60))
    wide_json.write_text("{\n" + wide_entries + "\n}\n", encoding="utf-8")

    # act
    pom_res = parser.parse_file(pom, tmp_path)
    xxe_res = parser.parse_file(pom_xxe, tmp_path)
    gradle_res = parser.parse_file(gradle, tmp_path)
    settings_res = parser.parse_file(settings_kts, tmp_path)
    props_res = parser.parse_file(props, tmp_path)

    # assert: java manifests and properties
    assert pom_res is not None
    pom_names = {e.name for e in pom_res.entities}
    assert {"org.acme", "billing-app", "core", "guice"}.issubset(pom_names)
    assert xxe_res is not None and xxe_res.entities == []

    assert gradle_res is not None
    gradle_names = {e.name for e in gradle_res.entities}
    assert {"java", "jvm", "spring-boot-starter-web", "shared-api"}.issubset(gradle_names)

    assert settings_res is not None
    assert {"core", "api"}.issubset({e.name for e in settings_res.entities})

    assert props_res is not None
    prop_names = {e.name for e in props_res.entities}
    assert {"spring.datasource.url", "multiline.query", "server.port"} == prop_names

    # assert: json bloat guards and 40-entity cap
    assert parser.parse_file(sample_json, tmp_path) is None
    assert parser.parse_file(huge_json, tmp_path) is None
    wide_res = parser.parse_file(wide_json, tmp_path)
    assert wide_res is not None
    assert len(wide_res.entities) == MAX_SATELLITE_ENTITIES == 40


# tarjan scc + bounded bfs on dense 100-node scc
def test_markdown_report_tarjan_scc_on_dense_100_node_graph() -> None:
    import time

    # arrange: complete directed 100-node SCC (exponential simple_cycles, fast Tarjan)
    g = nx.MultiDiGraph()
    node_count = 100
    for i in range(node_count):
        fpath = f"pkg/mod_{i:03d}.java"
        g.add_node(fpath, type=NodeType.FILE.value, file_path=fpath)
    for i in range(node_count):
        src = f"pkg/mod_{i:03d}.java"
        for offset in (1, 2, 5, 25):
            dst = f"pkg/mod_{(i + offset) % node_count:03d}.java"
            g.add_edge(src, dst, type=EdgeType.IMPORTS.value)

    comms = [
        CommunityInfo(
            id="C1", name="Core", god_node="pkg/mod_000.java", size=2, members=["a", "b"]
        ),
        CommunityInfo(id="C2", name="Iso", god_node="solo", size=1, members=["solo"]),
    ]

    # act
    t0 = time.perf_counter()
    report = render_markdown_report(g, comms, "dense_repo")
    elapsed = time.perf_counter() - t0

    # assert: fast completion and cycle warning
    assert elapsed < 2.0
    assert "## 4. Circular Dependency Warnings" in report
    assert "- Cycle:" in report
    assert "Isolated Singletons (size=1): 1 standalone entities" in report


# resource data dir bloat filters
def test_satellite_parser_skips_resource_data_dirs_keeps_normal_locales(tmp_path: Path) -> None:
    from astermesh.parser.satellite import SatelliteParser

    # arrange
    parser = SatelliteParser()

    spdx_json = tmp_path / "spdx" / "MIT.json"
    spdx_json.parent.mkdir(parents=True)
    spdx_json.write_text('{\n  "licenseId": "MIT"\n}\n', encoding="utf-8")

    lic_yaml = tmp_path / "waltz-jobs" / "src" / "main" / "resources" / "licence" / "a.yaml"
    lic_yaml.parent.mkdir(parents=True)
    lic_yaml.write_text("name: Apache\n", encoding="utf-8")

    res_i18n = tmp_path / "app" / "src" / "main" / "resources" / "static" / "i18n" / "en.json"
    res_i18n.parent.mkdir(parents=True)
    res_i18n.write_text('{\n  "hello": "Hello"\n}\n', encoding="utf-8")

    normal_locale = tmp_path / "ext" / "locales" / "en.json"
    normal_locale.parent.mkdir(parents=True)
    normal_locale.write_text('{\n  "title": "Demo"\n}\n', encoding="utf-8")

    app_yml = tmp_path / "app" / "src" / "main" / "resources" / "application.yml"
    app_yml.write_text("server:\n  port: 8080\n", encoding="utf-8")

    # act & assert
    assert parser.parse_file(spdx_json, tmp_path) is None
    assert parser.parse_file(lic_yaml, tmp_path) is None
    assert parser.parse_file(res_i18n, tmp_path) is None

    locale_res = parser.parse_file(normal_locale, tmp_path)
    assert locale_res is not None
    assert [e.name for e in locale_res.entities] == ["title"]

    yml_res = parser.parse_file(app_yml, tmp_path)
    assert yml_res is not None
    assert any(e.name == "server" for e in yml_res.entities)


