from pathlib import Path

from typer.testing import CliRunner

from astermesh.cli import app

runner = CliRunner()


def test_cli_build_acn_and_xml(tmp_path: Path):
    # arrange
    fpath = tmp_path / "hello.py"
    fpath.write_text("def say_hello(): print('hello')\n", encoding="utf-8")
    out_dir = tmp_path / "custom_output"

    # act: default dense acn format
    result_acn = runner.invoke(
        app, ["build", str(tmp_path), "--output-dir", str(out_dir), "--no-html"]
    )
    assert result_acn.exit_code == 0
    assert (out_dir / "context_graph.acn").exists()
    assert (out_dir / "GRAPH_REPORT.md").exists()

    # act: explicit xml format and cpm leiden mode
    result_xml = runner.invoke(
        app,
        [
            "build",
            str(tmp_path),
            "--output-dir",
            str(out_dir),
            "--format",
            "xml",
            "--leiden-mode",
            "cpm",
            "--no-html",
        ],
    )
    assert result_xml.exit_code == 0
    assert (out_dir / "context_graph.xml").exists()


def test_cli_query_overview_and_symbol(tmp_path: Path):
    # arrange
    fpath = tmp_path / "app.py"
    fpath.write_text("class CoreApp:\n    def start(self): pass\n", encoding="utf-8")

    # act: overview
    res_overview = runner.invoke(app, ["query", "-p", str(tmp_path), "--overview"])
    assert res_overview.exit_code == 0
    assert "Repository Overview" in res_overview.stdout

    # act: locate symbol by suffix dot-path
    res_symbol = runner.invoke(app, ["query", "CoreApp.start", "-p", str(tmp_path)])
    assert res_symbol.exit_code == 0
    assert "CoreApp.start" in res_symbol.stdout
    assert "app.py" in res_symbol.stdout


def test_cli_benchmark(tmp_path: Path):
    # arrange
    fpath = tmp_path / "test.py"
    fpath.write_text("def alpha():\n    return 10\n", encoding="utf-8")
    report_file = tmp_path / "BENCHMARK.md"

    # act
    result = runner.invoke(app, ["benchmark", str(tmp_path), "--report", str(report_file)])

    # assert
    assert result.exit_code == 0
    assert report_file.exists()
    text = report_file.read_text(encoding="utf-8")
    assert "Token Benchmark Report" in text
    assert "Dense ACN Wire" in text
