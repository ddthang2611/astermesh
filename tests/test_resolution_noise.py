from pathlib import Path

import networkx as nx

from astermesh.graph.builder import GraphBuilder
from astermesh.graph.filters import is_ignored_dir, is_ignored_file, is_test_path
from astermesh.graph.model import EdgeType, NodeType


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def _out(g: nx.MultiDiGraph, node: str) -> set[tuple[str, str]]:
    return {(v, d["type"]) for _, v, d in g.out_edges(node, data=True)}


# kiem thu bo loc thu muc, tep bo qua va duong dan test
def test_ignore_rules_for_codegen_and_test_paths():
    assert is_ignored_dir(".git")
    assert is_ignored_dir("foo/.venv/bar")
    assert is_ignored_dir("astermesh-graph")
    assert is_ignored_dir("extensions/foo/generated")
    assert is_ignored_dir("src/__generated__/schema")
    assert not is_ignored_dir("src/astermesh")
    assert not is_ignored_dir(".")

    assert is_ignored_file("vendor.min.js")
    assert is_ignored_file("app.bundle.js")
    assert is_ignored_file("app.js.map")
    assert is_ignored_file("schema.generated.ts")
    assert not is_ignored_file("generator.ts")
    assert not is_ignored_file("App.tsx")

    assert is_test_path("src/rules.test.ts")
    assert is_test_path("src/Button.spec.tsx")
    assert is_test_path("extensions/a/tests/fixtures/cart.json")
    assert is_test_path("src/__mocks__/api.ts")
    assert is_test_path("tests/test_api.py")
    assert not is_test_path("src/testing-utils.ts")
    assert not is_test_path("src/contest/page.tsx")


# kiem thu bo qua codegen va fixture json trong thu muc test
def test_generated_dirs_and_test_fixtures_are_skipped(tmp_path: Path):
    _write(tmp_path, {
        "ext/generated/api.ts": "export type Cart = { id: string };\n",
        "ext/src/__generated__/schema.ts": "export type Shop = { id: string };\n",
        "ext/src/types.generated.ts": "export type Line = { id: string };\n",
        "ext/tests/fixtures/cart.json": '{\n  "cart": 1\n}\n',
        "ext/src/__mocks__/data.json": '{\n  "mock": 1\n}\n',
        "ext/tests/run.test.ts": "export const helper = 1;\n",
        "ext/locales/en.json": '{\n  "title": "Hi"\n}\n',
        "ext/src/main.ts": "export function main() { return 1; }\n",
    })

    g = GraphBuilder(tmp_path).build_full()
    files = {n for n, d in g.nodes(data=True) if d.get("type") == NodeType.FILE.value}

    assert files == {"ext/tests/run.test.ts", "ext/locales/en.json", "ext/src/main.ts"}


# kiem thu bien cuc bo khong bi noi sang symbol trung ten o file khac
def test_local_bindings_shadow_cross_file_symbols(tmp_path: Path):
    _write(tmp_path, {
        "src/shared.ts": (
            "export const ids = ['x'];\n"
            "export const fmt = (s: string) => s;\n"
            "export const isGift = true;\n"
            "export function Row() { return null; }\n"
        ),
        "src/tiers.ts": (
            "export function parseTiers(raw: any[]) {\n"
            "  const ids = raw.map((t) => t.id);\n"
            "  const fmt = (x: string) => x.trim();\n"
            "  return ids.map(fmt);\n"
            "}\n"
        ),
        "src/Nudge.tsx": (
            "export function Nudge({ isGift, row: Row }: any) {\n"
            "  return isGift ? <Row /> : null;\n"
            "}\n"
        ),
        "src/handlers.ts": (
            "export function onError() {\n"
            "  try { return 1; } catch (ids) { return ids; }\n"
            "}\n"
            "export function loop(list: any[]) {\n"
            "  for (const fmt of list) { fmt(); }\n"
            "}\n"
        ),
    })

    g = GraphBuilder(tmp_path).build_full()

    def cross(node: str) -> set[str]:
        return {v for v, _ in _out(g, node) if v.startswith("src.shared.")}

    assert cross("src.tiers.parseTiers") == set()
    assert cross("src.Nudge.Nudge") == set()
    assert cross("src.handlers.onError") == set()
    assert cross("src.handlers.loop") == set()


# kiem thu fallback toan cuc chi chon symbol export, khong chon test/config
def test_global_fallback_filters_candidates(tmp_path: Path):
    _write(tmp_path, {
        "src/rules.test.ts": "const ids = ['a'];\nconst isGift = true;\n",
        "src/private.ts": "const secret = 1;\nfunction hidden() { return 1; }\n",
        "src/en.default.json": '{\n  "isGift": "Gift"\n}\n',
        "src/consumer.ts": (
            "export function use() {\n"
            "  hidden();\n"
            "  return [ids, isGift, secret];\n"
            "}\n"
        ),
    })

    g = GraphBuilder(tmp_path).build_full()

    assert _out(g, "src.consumer.use") == set()


# kiem thu cac canh dung van con sau khi siet resolve
def test_legit_edges_still_resolve(tmp_path: Path):
    _write(tmp_path, {
        "src/api.ts": (
            "export const BASE = '/api';\n"
            "export function fetchUser(id: string) { return fetch(BASE + id); }\n"
        ),
        "src/Avatar.tsx": "function Avatar() { return <img />; }\nexport default Avatar;\n",
        "src/utils.ts": (
            "const clamp = (n: number) => n;\n"
            "const pad = (s: string) => s;\n"
            "export { clamp, pad as padStr };\n"
        ),
        "src/Profile.tsx": (
            "import Avatar from './Avatar';\n"
            "import { fetchUser, BASE } from './api';\n"
            "import { clamp } from './utils';\n"
            "const LIMIT = 3;\n"
            "function helper() { return LIMIT; }\n"
            "export function Profile({ id }: { id: string }) {\n"
            "  fetchUser(id);\n"
            "  helper();\n"
            "  clamp(LIMIT);\n"
            "  return <Avatar />;\n"
            "}\n"
        ),
        "src/rules.test.ts": (
            "import { fetchUser } from './api';\n"
            "test('x', () => { fetchUser('1'); });\n"
        ),
    })

    g = GraphBuilder(tmp_path).build_full()

    profile = _out(g, "src.Profile.Profile")
    assert ("src.api.fetchUser", EdgeType.CALLS.value) in profile
    assert ("src.Profile.helper", EdgeType.CALLS.value) in profile
    assert ("src.utils.clamp", EdgeType.CALLS.value) in profile
    assert ("src.Avatar.Avatar", EdgeType.RENDERS.value) in profile
    assert ("src.Profile.LIMIT", EdgeType.REFERENCES.value) in profile
    assert ("src.api.BASE", EdgeType.REFERENCES.value) in _out(g, "src.api.fetchUser")

    # export { } va export default danh dau is_exported
    assert g.nodes["src.Avatar.Avatar"]["is_exported"]
    assert g.nodes["src.utils.clamp"]["is_exported"]
    assert g.nodes["src.utils.pad"]["is_exported"]

    # test file van goi duoc code production
    test_edges = {v for _, v, d in g.out_edges(data=True) if d["type"] == EdgeType.CALLS.value}
    assert "src.api.fetchUser" in test_edges


# kiem thu watcher: file fixture moi trong thu muc test cung bi bo qua
def test_incremental_update_respects_ignore_rules(tmp_path: Path):
    _write(tmp_path, {"src/main.ts": "export const a = 1;\n"})
    builder = GraphBuilder(tmp_path)
    builder.build_full()

    fixture = tmp_path / "tests" / "fixtures" / "x.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text('{\n  "k": 1\n}\n', encoding="utf-8")
    builder.update_file(fixture)

    gen = tmp_path / "src" / "api.generated.ts"
    gen.write_text("export type T = 1;\n", encoding="utf-8")
    builder.update_file(gen)

    assert not builder.graph.has_node("tests/fixtures/x.json")
    assert not builder.graph.has_node("src/api.generated.ts")


# kiem thu uu tien phan giai export mac dinh trong linker
def test_linker_resolve_target_export_priorities(tmp_path: Path):
    _write(tmp_path, {
        "src/widget.ts": (
            "function internalHelper() { return 0; }\n"
            "export function exportedAction() { return 1; }\n"
        ),
        "src/caller.ts": (
            "import widgetDefault from './widget';\n"
            "export function run() { return widgetDefault(); }\n"
        ),
    })

    g = GraphBuilder(tmp_path).build_full()
    assert ("src.widget.exportedAction", EdgeType.CALLS.value) in _out(g, "src.caller.run")


# barrel re-exports and cycles
def test_barrel_reexports_and_cycle_guard(tmp_path: Path):
    _write(tmp_path, {
        "src/core/alpha.ts": "export function alphaFn() { return 1; }\n",
        "src/core/beta.ts": (
            "export function rawBeta() { return 2; }\n"
            "export function helperBeta() { return 4; }\n"
            "export default function BetaComp() { return rawBeta(); }\n"
        ),
        "src/core/gamma.ts": "export function gammaFn() { return 3; }\n",
        "src/core/index.ts": (
            "export * from './alpha';\n"
            "export { rawBeta as betaFn, helperBeta, default as Beta } from './beta';\n"
            "export * as gammaNs from './gamma';\n"
        ),
        "src/local_barrel.ts": (
            "import { helperBeta } from './core';\n"
            "export { helperBeta as aliasedBeta };\n"
        ),
        "src/cycle_a.ts": "export * from './cycle_b';\n",
        "src/cycle_b.ts": "export * from './cycle_a';\n",
        "src/app.ts": (
            "import { alphaFn, betaFn, Beta, gammaNs } from './core';\n"
            "import { aliasedBeta } from './local_barrel';\n"
            "import { missingFn } from './cycle_a';\n"
            "export function run() {\n"
            "  alphaFn();\n"
            "  betaFn();\n"
            "  Beta();\n"
            "  gammaNs.gammaFn();\n"
            "  aliasedBeta();\n"
            "  missingFn();\n"
            "}\n"
        ),
    })

    g = GraphBuilder(tmp_path).build_full()
    run_out = _out(g, "src.app.run")

    assert ("src.core.alpha.alphaFn", EdgeType.CALLS.value) in run_out
    assert ("src.core.beta.rawBeta", EdgeType.CALLS.value) in run_out
    assert ("src.core.beta.BetaComp", EdgeType.CALLS.value) in run_out
    assert ("src.core.gamma.gammaFn", EdgeType.CALLS.value) in run_out
    assert ("src.core.beta.helperBeta", EdgeType.CALLS.value) in run_out
    assert ("src/local_barrel.ts", EdgeType.IMPORTS.value) in {
        (v, d["type"]) for _, v, d in g.out_edges("src/app.ts", data=True)
    }
    assert ("src/cycle_a.ts", EdgeType.IMPORTS.value) in {
        (v, d["type"]) for _, v, d in g.out_edges("src/app.ts", data=True)
    }


# block bogus dotted receiver calls
def test_unresolved_dotted_member_call_does_not_leak_globally(tmp_path: Path):
    _write(tmp_path, {
        "src/validator.ts": (
            "export class TargetValidator {\n"
            "  validate(v: string) { return v.length > 0; }\n"
            "  check() { return this.validate('ok'); }\n"
            "}\n"
        ),
        "src/noise.js": (
            "export function handle(unknownObj, holder) {\n"
            "  unknownObj.validate('x');\n"
            "  this.holder.validate('y');\n"
            "  this.validate('z');\n"
            "  return 1;\n"
            "}\n"
        ),
        "src/direct.ts": (
            "import { TargetValidator } from './validator';\n"
            "export function verify() {\n"
            "  return TargetValidator.validate('a');\n"
            "}\n"
        ),
    })

    g = GraphBuilder(tmp_path).build_full()

    assert _out(g, "src.noise.handle") == set()
    assert ("src.validator.TargetValidator.validate", EdgeType.CALLS.value) in _out(
        g, "src.validator.TargetValidator.check"
    )
    assert ("src.validator.TargetValidator.validate", EdgeType.CALLS.value) in _out(
        g, "src.direct.verify"
    )
