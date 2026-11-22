from pathlib import Path

from watchdog.events import FileCreatedEvent

from astermesh.graph.builder import GraphBuilder
from astermesh.graph.filters import (
    is_ignored_file,
    is_minified_js,
    is_saved_webpage_dir,
)
from astermesh.graph.watcher import GraphEventHandler


# filter saved webpage dirs
def test_saved_webpage_dir_filtered_in_builder_and_watcher(tmp_path: Path):
    (tmp_path / "Page.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "Page_files").mkdir()
    (tmp_path / "Page_files/vendor.js").write_text("export function noise() {}\n", encoding="utf-8")
    (tmp_path / "media_files").mkdir()
    (tmp_path / "media_files/real.py").write_text(
        "def keep_me():\n    return 1\n", encoding="utf-8"
    )

    assert is_saved_webpage_dir("Page_files", {"Page.html"})
    assert is_saved_webpage_dir("page_files", {"Page.HTM"})
    assert not is_saved_webpage_dir("media_files", {"other.html"})

    builder = GraphBuilder(tmp_path)
    g = builder.build_full()

    assert not g.has_node("Page_files/vendor.js")
    assert not g.has_node("Page_files.vendor.noise")
    assert g.has_node("media_files/real.py")
    assert g.has_node("media_files.real.keep_me")

    # watcher ignores new file inside saved webpage dir
    handler = GraphEventHandler(builder, debounce_sec=0.01)
    saved_extra = tmp_path / "Page_files/extra.js"
    saved_extra.write_text("export function extra() {}\n", encoding="utf-8")
    handler.on_any_event(FileCreatedEvent(str(saved_extra)))
    assert not builder.graph.has_node("Page_files/extra.js")


# ignore minified, hashed, and duplicate JS
def test_minified_hashed_and_duplicate_js_filtering(tmp_path: Path):
    assert is_ignored_file("lib.iife.js")
    assert is_ignored_file("lib.umd.js")
    assert is_ignored_file("lib.esm.min.js")
    assert is_ignored_file("script (1).js")
    assert is_ignored_file("chunk.0123456789abcdef.mjs")
    assert not is_ignored_file("app.config.js")

    minified = tmp_path / "bundled.js"
    minified.write_text("const a=" + "+".join(["1"] * 1200) + ";", encoding="utf-8")
    assert is_minified_js(minified)

    # one long literal with short average lines stays indexed
    readable = tmp_path / "readable.js"
    body_lines = ["export function f() {"] + ["  const x = 1;"] * 20
    body_lines.append("  const big = '" + ("a" * 1050) + "';")
    body_lines.append("  return x;\n}")
    readable.write_text("\n".join(body_lines), encoding="utf-8")
    assert not is_minified_js(readable)

    builder = GraphBuilder(tmp_path)
    g = builder.build_full()
    assert not g.has_node("bundled.js")
    assert g.has_node("readable.js")
    assert g.has_node("readable.f")


# dynamic exclude globs and .gitignore
def test_exclude_patterns_and_gitignore_in_builder_and_watcher(tmp_path: Path):
    (tmp_path / ".gitignore").write_text(
        "# ignored dirs\nignored_dir/\n*.skip.py\n", encoding="utf-8"
    )
    (tmp_path / "resources").mkdir()
    (tmp_path / "resources/asset.js").write_text("export function r() {}\n", encoding="utf-8")
    (tmp_path / "ignored_dir").mkdir()
    (tmp_path / "ignored_dir/skip.py").write_text("def hidden(): pass\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("def run(): return 1\n", encoding="utf-8")
    (tmp_path / "src/a.skip.py").write_text("def skipped(): pass\n", encoding="utf-8")

    builder = GraphBuilder(tmp_path, exclude_patterns=["resources/**"], respect_gitignore=True)
    g = builder.build_full()

    assert g.has_node("src.app.run")
    assert not g.has_node("resources/asset.js")
    assert not g.has_node("ignored_dir/skip.py")
    assert not g.has_node("src/a.skip.py")

    # watcher respects exclude patterns and gitignore
    handler = GraphEventHandler(builder, debounce_sec=0.01)
    new_res = tmp_path / "resources/added.js"
    new_res.write_text("export function added() {}\n", encoding="utf-8")
    handler.on_any_event(FileCreatedEvent(str(new_res)))
    assert not builder.graph.has_node("resources/added.js")
