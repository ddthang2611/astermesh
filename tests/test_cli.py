from pathlib import Path

import pytest
from typer.testing import CliRunner

from astermesh.cli import app

runner = CliRunner()


# kiem thu cli build acn2, ngan sach token va co xml legacy
def test_cli_build_acn_and_legacy_xml(tmp_path: Path):
    fpath = tmp_path / "hello.py"
    fpath.write_text("def say_hello(): print('hello')\n", encoding="utf-8")
    out_dir = tmp_path / "custom_output"

    res_default = runner.invoke(app, ["build", str(tmp_path), "--no-html"])
    assert res_default.exit_code == 0
    assert (tmp_path / "astermesh-graph" / "context_graph.acn").exists()
    assert not (tmp_path / "astermesh-graph" / "context_graph.xml").exists()

    custom_acn = out_dir / "direct.acn"
    res_direct = runner.invoke(
        app,
        [
            "build",
            str(tmp_path),
            "-o",
            str(custom_acn),
            "--max-tokens",
            "500",
            "--leiden-mode",
            "cpm",
            "--no-html",
        ],
    )
    assert res_direct.exit_code == 0
    assert custom_acn.exists()
    assert (out_dir / "GRAPH_REPORT.md").exists()
    assert not (out_dir / "context_graph.xml").exists()

    with pytest.warns(DeprecationWarning):
        res_legacy = runner.invoke(
            app,
            [
                "build",
                str(tmp_path),
                "--output-dir",
                str(out_dir),
                "--include-xml-legacy",
                "--no-html",
            ],
        )
    assert res_legacy.exit_code == 0
    assert (out_dir / "context_graph.xml").exists()


# kiem thu cli query overview, communities va ky hieu
def test_cli_query_overview_communities_and_symbol(tmp_path: Path):
    fpath = tmp_path / "app.py"
    fpath.write_text("class CoreApp:\n    def start(self): pass\n", encoding="utf-8")

    res_overview = runner.invoke(app, ["query", "-p", str(tmp_path), "--overview"])
    assert res_overview.exit_code == 0
    assert "Repository Overview" in res_overview.stdout

    res_comms = runner.invoke(app, ["query", "-p", str(tmp_path), "--list-communities"])
    assert res_comms.exit_code == 0
    assert "Discovered Communities" in res_comms.stdout

    res_symbol = runner.invoke(app, ["query", "CoreApp.start", "-p", str(tmp_path)])
    assert res_symbol.exit_code == 0
    assert "CoreApp.start" in res_symbol.stdout
    assert "app.py" in res_symbol.stdout

    res_missing = runner.invoke(app, ["query", "DoesNotExist", "-p", str(tmp_path)])
    assert res_missing.exit_code == 0
    assert "not found" in res_missing.stdout


# kiem thu cli benchmark token va bao cao markdown
def test_cli_benchmark(tmp_path: Path):
    fpath = tmp_path / "test.py"
    fpath.write_text("def alpha():\n    return 10\n", encoding="utf-8")
    report_file = tmp_path / "BENCHMARK.md"

    result = runner.invoke(app, ["benchmark", str(tmp_path), "--report", str(report_file)])
    assert result.exit_code == 0
    assert report_file.exists()
    text = report_file.read_text(encoding="utf-8")
    assert "Token Benchmark Report" in text
    assert "Dense ACN Wire" in text
    assert "N/A (opt-in)" in text

    res_xml = runner.invoke(
        app,
        ["benchmark", str(tmp_path), "--include-xml-legacy", "--max-tokens", "200"],
    )
    assert res_xml.exit_code == 0
    assert "smaller than XML" in res_xml.stdout


# test cli --exclude and --respect-gitignore flags
def test_cli_exclude_and_respect_gitignore(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("gitignored/\n", encoding="utf-8")
    (tmp_path / "gitignored").mkdir()
    (tmp_path / "gitignored/a.py").write_text("def skip_git(): pass\n", encoding="utf-8")
    (tmp_path / "resources").mkdir()
    (tmp_path / "resources/b.py").write_text("def skip_res(): pass\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/keep.py").write_text("def keep_fn(): return 1\n", encoding="utf-8")

    res_build = runner.invoke(
        app,
        [
            "build",
            str(tmp_path),
            "--no-html",
            "--exclude",
            "resources/**",
            "--respect-gitignore",
        ],
    )
    assert res_build.exit_code == 0
    acn = (tmp_path / "astermesh-graph" / "context_graph.acn").read_text(encoding="utf-8")
    assert "keep_fn" in acn
    assert "skip_res" not in acn
    assert "skip_git" not in acn

    res_query = runner.invoke(
        app,
        [
            "query",
            "skip_res",
            "-p",
            str(tmp_path),
            "--exclude",
            "resources/**",
            "--respect-gitignore",
        ],
    )
    assert res_query.exit_code == 0
    assert "not found" in res_query.stdout
