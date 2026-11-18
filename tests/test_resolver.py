from pathlib import Path

import pytest

from astermesh.parser.resolver import (
    ModuleResolver,
    _strip_jsonc,
    is_asset_path,
    split_bundler_query,
)


def test_split_bundler_query():
    assert split_bundler_query("./logo.svg?react") == ("./logo.svg", "?react")
    assert split_bundler_query("./shader.glsl?raw") == ("./shader.glsl", "?raw")
    assert split_bundler_query("./doc.html#section") == ("./doc.html", "#section")
    assert split_bundler_query("./utils") == ("./utils", None)


def test_resolver_tsconfig_trie_and_suffix_probe(tmp_path: Path):
    # arrange
    (tmp_path / "tsconfig.json").write_text(
        """{
  // comment inside tsconfig
  "compilerOptions": {
    "baseUrl": ".",
    "paths": {
      "@/*": ["src/*"],
      "@ui/*": ["src/components/ui/*"]
    }
  }
}
""",
        encoding="utf-8",
    )
    (tmp_path / "src/components/ui/Button").mkdir(parents=True)
    btn_index = tmp_path / "src/components/ui/Button/index.tsx"
    btn_index.write_text("export const Button = () => null;", encoding="utf-8")

    (tmp_path / "src/assets").mkdir(parents=True)
    logo = tmp_path / "src/assets/logo.svg"
    logo.write_text("<svg/>", encoding="utf-8")

    caller = tmp_path / "src/pages/Home.tsx"
    caller.parent.mkdir(parents=True)
    caller.write_text("", encoding="utf-8")

    resolver = ModuleResolver(tmp_path)

    # act & assert: alias trie longest match and index probe
    path_ui, flag_ui = resolver.resolve("src/pages/Home.tsx", "@ui/Button")
    assert path_ui == "src/components/ui/Button/index.tsx"
    assert flag_ui is None

    # act & assert: bundler query flag preserved with clean posix path
    path_logo, flag_logo = resolver.resolve("src/pages/Home.tsx", "@/assets/logo.svg?react")
    assert path_logo == "src/assets/logo.svg"
    assert flag_logo == "?react"

    # act & assert: external package returns None
    path_ext, _ = resolver.resolve("src/pages/Home.tsx", "react")
    assert path_ext is None


def test_resolver_security_boundary(tmp_path: Path):
    # arrange
    outside = tmp_path.parent / "secret.ts"
    outside.write_text("export const x = 1;", encoding="utf-8")
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src/app.ts").write_text("", encoding="utf-8")

    resolver = ModuleResolver(workspace)

    # act
    resolved, _ = resolver.resolve("src/app.ts", "../../secret")

    # assert
    assert resolved is None


# kiem thu bo loc jsonc
def test_strip_jsonc_preserves_strings_and_strips_comments():
    raw = """{
  // comment line
  "compilerOptions": {
    "baseUrl": "https://example.com/a/*keep*/b", /* block comment */
    "paths": { "@/*": ["src/*"], },
  },
}"""
    cleaned = _strip_jsonc(raw)
    assert "https://example.com/a/*keep*/b" in cleaned
    assert "comment line" not in cleaned
    assert "block comment" not in cleaned


# kiem thu jsconfig, baseUrl, public fallback va cache lru
def test_resolver_edge_cases_and_lru(tmp_path: Path):
    (tmp_path / "jsconfig.json").write_text(
        """{
  "compilerOptions": {
    "baseUrl": "src",
    "paths": {
      "@config": ["config/app.ts"],
      "@multi/*": ["override/*", "shared/*"]
    }
  }
}""",
        encoding="utf-8",
    )
    (tmp_path / "src/config").mkdir(parents=True)
    (tmp_path / "src/config/app.ts").write_text("export const x = 1;", encoding="utf-8")
    (tmp_path / "src/shared").mkdir(parents=True)
    (tmp_path / "src/shared/View.vue").write_text("<template/>", encoding="utf-8")
    (tmp_path / "src/utils").mkdir(parents=True)
    (tmp_path / "src/utils/date.mts").write_text("export const d = 1;", encoding="utf-8")
    (tmp_path / "src/utils/index.ts").write_text("export const i = 1;", encoding="utf-8")
    (tmp_path / "public/img").mkdir(parents=True)
    (tmp_path / "public/img/hero.png").write_text("PNG", encoding="utf-8")

    resolver = ModuleResolver(tmp_path, max_cache=2)

    assert resolver.resolve("src/App.ts", "@config")[0] == "src/config/app.ts"
    assert resolver.resolve("src/App.ts", "@config/extra")[0] is None
    assert resolver.resolve("src/App.ts", "@multi/View")[0] == "src/shared/View.vue"
    assert resolver.resolve("src/App.ts", "utils/date")[0] == "src/utils/date.mts"
    assert resolver.resolve("src/utils/date.mts", ".")[0] == "src/utils/index.ts"
    assert resolver.resolve("src/App.ts", "/img/hero.png")[0] == "public/img/hero.png"
    assert is_asset_path("icons/LOGO.SVG")
    assert len(resolver._cache) <= 2

    resolver.set_known_files(set())
    assert len(resolver._cache) == 0


# kiem thu bien bao mat cho alias
def test_resolver_alias_boundary_rejected(tmp_path: Path):
    outside = tmp_path.parent / "leak.ts"
    outside.write_text("export const s = 1;", encoding="utf-8")
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "tsconfig.json").write_text(
        '{"compilerOptions": {"paths": {"@leak/*": ["../../*"]}}}',
        encoding="utf-8",
    )
    (repo / "src").mkdir()
    (repo / "src/main.ts").write_text("", encoding="utf-8")

    resolver = ModuleResolver(repo)
    assert resolver.resolve("src/main.ts", "@leak/leak")[0] is None


# lexical guard, dir cache, symlink check
def test_resolver_lexical_reject_cached_dir_and_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside.ts"
    outside.write_text("export const leak = 1;\n", encoding="utf-8")

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src/app.ts").write_text("", encoding="utf-8")
    (repo / "src/real.ts").write_text("export const ok = 1;\n", encoding="utf-8")
    (repo / "tsconfig.json").write_text(
        '{"compilerOptions": {"paths": {"@bad/*": ["../../outside/*"]}}}',
        encoding="utf-8",
    )

    escape_link = repo / "src/evil.ts"
    escape_link.symlink_to(outside)

    resolver = ModuleResolver(repo)

    resolve_calls = 0
    orig_resolve = Path.resolve

    def counted_resolve(self: Path, strict: bool = False) -> Path:
        nonlocal resolve_calls
        resolve_calls += 1
        return orig_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", counted_resolve)

    # lexical traversal misses avoid Path.resolve
    assert resolver.resolve("src/app.ts", "../outside")[0] is None
    assert resolver.resolve("src/app.ts", "@bad/secret")[0] is None
    assert resolver.resolve("src/app.ts", "./does_not_exist")[0] is None
    assert resolve_calls == 0

    # known files hit in memory
    resolver.set_known_files({"src/app.ts", "src/real.ts"})
    assert resolver.resolve("src/app.ts", "./real")[0] == "src/real.ts"
    assert resolve_calls == 0

    # symlink pointing outside workspace is blocked
    assert resolver.resolve("src/app.ts", "./evil")[0] is None
    assert resolver.resolve("src/app.ts", "./real")[0] == "src/real.ts"
    assert "src" in resolver._dir_entries or resolver._cache

    resolver.set_known_files(set())
    assert resolver._dir_entries == {}
    assert len(resolver._cache) == 0


# map NodeNext JS imports to TS
def test_resolver_nodenext_js_specifier_maps_to_ts_source(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.ts").write_text("", encoding="utf-8")
    (tmp_path / "src/foo.ts").write_text("export const x = 1;\n", encoding="utf-8")
    (tmp_path / "src/foo.js").write_text("export const x = 1;\n", encoding="utf-8")
    (tmp_path / "src/Bar.tsx").write_text("export const Bar = () => null;\n", encoding="utf-8")
    (tmp_path / "src/mod.mts").write_text("export const m = 1;\n", encoding="utf-8")
    (tmp_path / "src/cfg.cts").write_text("export const c = 1;\n", encoding="utf-8")
    (tmp_path / "src/real.js").write_text("export const r = 1;\n", encoding="utf-8")

    resolver = ModuleResolver(tmp_path)
    resolver.set_known_files({
        "src/main.ts",
        "src/foo.ts",
        "src/Bar.tsx",
        "src/mod.mts",
        "src/cfg.cts",
        "src/real.js",
    })

    # prefer indexed TS over emitted JS
    assert resolver.resolve("src/main.ts", "./foo.js")[0] == "src/foo.ts"
    assert resolver.resolve("src/main.ts", "./Bar.jsx")[0] == "src/Bar.tsx"
    assert resolver.resolve("src/main.ts", "./mod.mjs")[0] == "src/mod.mts"
    assert resolver.resolve("src/main.ts", "./cfg.cjs")[0] == "src/cfg.cts"
    assert resolver.resolve("src/main.ts", "./real.js")[0] == "src/real.js"
    assert resolver.resolve("src/main.ts", "./missing.js")[0] is None

    # disk fallback when known_files is empty
    disk_resolver = ModuleResolver(tmp_path)
    assert disk_resolver.resolve("src/main.ts", "./Bar.jsx")[0] == "src/Bar.tsx"
    assert disk_resolver.resolve("src/main.ts", "./mod.mjs")[0] == "src/mod.mts"
    assert disk_resolver.resolve("src/main.ts", "./cfg.cjs")[0] == "src/cfg.cts"
