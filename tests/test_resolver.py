from pathlib import Path

from astermesh.parser.resolver import ModuleResolver, _strip_jsonc, is_asset_path, split_bundler_query


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
