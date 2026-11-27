from pathlib import Path

import tree_sitter

from astermesh.graph.filters import is_minified_js, is_vendor_js_path
from astermesh.graph.filters import is_test_path as _is_test_path
from astermesh.graph.model import EdgeType, NodeType
from astermesh.languages.base import LanguageLinkStrategy, LanguagePlugin, LinkContext
from astermesh.parser.extractors.sfc import parse_sfc
from astermesh.parser.extractors.ts_js import extract_ts_js
from astermesh.parser.resolver import is_asset_path
from astermesh.parser.types import FileParseResult, ImportSpec, RawEntity, ReexportSpec

_FAMILY = "js_ts_web"
_SFC_EXTENSIONS = frozenset({".vue", ".svelte"})
_JS_SUFFIXES = frozenset({".js", ".jsx", ".mjs", ".cjs"})
_TS_SUFFIXES = (".ts", ".tsx", ".mts", ".cts")
_JS_FALLBACK_SUFFIXES: dict[str, tuple[str, ...]] = {
    ".ts": (".js", ".jsx", ".mjs", ".cjs"),
    ".tsx": (".jsx", ".js", ".mjs", ".cjs"),
    ".mts": (".mjs", ".js", ".jsx", ".cjs"),
    ".cts": (".cjs", ".js", ".jsx", ".mjs"),
}
_NON_CODE_TYPES = frozenset({
    NodeType.CONFIG.value,
    NodeType.DOC.value,
    NodeType.FOLDER.value,
    NodeType.FILE.value,
    NodeType.ASSET.value,
})
_CLASS_TYPES = frozenset({
    NodeType.CLASS.value,
    NodeType.SERVICE.value,
    NodeType.COMPONENT.value,
})


def _has_ts_sibling(fpath: Path) -> bool:
    if fpath.suffix.lower() not in _JS_SUFFIXES:
        return False
    return any(fpath.with_suffix(s).is_file() for s in _TS_SUFFIXES)


def _path_to_js_module(rel_path: str) -> str:
    parts = list(Path(rel_path).with_suffix("").parts)
    return ".".join(parts) if parts else "root"


# js/ts/web pass-2 linking strategy
class FilePathLinkStrategy(LanguageLinkStrategy):
    """Link JS/TS/Web imports, barrels, renders, and calls."""

    def link_file(self, parsed: FileParseResult, ctx: LinkContext) -> None:
        """Link JS/TS/Web relations for one file."""
        rel_file = parsed.file_path
        local_aliases, asset_aliases = self._link_imports(parsed, ctx)
        consumed_assets: set[str] = set()

        class_injects: dict[str, list[str]] = {
            ent.fqn: ent.injects for ent in parsed.entities if ent.injects
        }
        class_owners: dict[str, list[str]] = {
            ent.fqn: [ent.fqn, *ent.bases]
            for ent in parsed.entities
            if ent.kind in _CLASS_TYPES
        }

        for ent in parsed.entities:
            extra_classes = self._lookup_enclosing(ent.parent_fqn, class_injects)
            owner_classes = self._lookup_enclosing(ent.parent_fqn, class_owners)
            self._link_entity_relations(
                ent,
                parsed,
                ctx,
                local_aliases,
                asset_aliases,
                extra_classes,
                consumed_assets,
                owner_classes,
            )

        for alias_name, (asset_id, _) in asset_aliases.items():
            if alias_name not in consumed_assets:
                ctx.add_edge_fn(rel_file, asset_id, EdgeType.IMPORTS)

    @staticmethod
    def _lookup_enclosing(fqn: str | None, table: dict[str, list[str]]) -> list[str]:
        curr = fqn or ""
        while curr:
            if curr in table:
                return table[curr]
            if "." not in curr:
                break
            curr = curr.rsplit(".", 1)[0]
        return []

    def _link_imports(
        self, parsed: FileParseResult, ctx: LinkContext
    ) -> tuple[dict[str, str], dict[str, tuple[str, str | None]]]:
        rel_file = parsed.file_path
        local_aliases: dict[str, str] = {}
        asset_aliases: dict[str, tuple[str, str | None]] = {}

        for alias, target_mod in parsed.imports.items():
            resolved = self._resolve_symbol(target_mod, parsed, ctx)
            if resolved and ctx.graph.has_node(resolved):
                ctx.add_edge_fn(rel_file, resolved, EdgeType.IMPORTS)
                local_aliases[alias] = resolved

        for imp in parsed.raw_import_specs:
            resolved_path, query_flag = ctx.resolver.resolve(rel_file, imp.raw_specifier)
            if not resolved_path:
                continue

            if resolved_path not in ctx.index.parsed_files and is_asset_path(resolved_path):
                asset_id = ctx.ensure_asset_fn(resolved_path)
                if imp.local_name:
                    asset_aliases[imp.local_name] = (asset_id, query_flag)
                else:
                    ctx.add_edge_fn(rel_file, asset_id, EdgeType.IMPORTS)
                continue

            if ctx.graph.has_node(resolved_path):
                ctx.add_edge_fn(rel_file, resolved_path, EdgeType.IMPORTS)
                if imp.local_name:
                    target_fqn = self._resolve_target_export(resolved_path, imp, ctx)
                    if target_fqn:
                        local_aliases[imp.local_name] = target_fqn

        return local_aliases, asset_aliases

    def _link_entity_relations(
        self,
        ent: RawEntity,
        parsed: FileParseResult,
        ctx: LinkContext,
        local_aliases: dict[str, str],
        asset_aliases: dict[str, tuple[str, str | None]],
        extra_classes: list[str],
        consumed_assets: set[str],
        owner_classes: list[str],
    ) -> None:
        for rendered in ent.renders:
            if self._try_link_asset(
                ent.fqn, rendered, asset_aliases, consumed_assets, ctx, force_render=True
            ):
                continue
            target = self._resolve_symbol(
                rendered,
                parsed,
                ctx,
                local_aliases,
                shadowed=ent.local_names,
                owner_classes=owner_classes,
            )
            ctx.add_entity_edge_fn(ent.fqn, target, EdgeType.RENDERS)

        for names, etype in (
            (ent.injects, EdgeType.INJECTS),
            (ent.provides, EdgeType.PROVIDES),
            (ent.bases, EdgeType.INHERITS),
        ):
            for name in names:
                target = self._resolve_symbol(name, parsed, ctx, local_aliases)
                ctx.add_entity_edge_fn(ent.fqn, target, etype)

        for call in ent.calls:
            if self._try_link_asset(
                ent.fqn, call, asset_aliases, consumed_assets, ctx, force_render=False
            ):
                continue
            target = self._resolve_symbol(
                call,
                parsed,
                ctx,
                local_aliases,
                hint_classes=extra_classes,
                shadowed=ent.local_names,
                owner_classes=owner_classes,
            )
            ctx.add_entity_edge_fn(ent.fqn, target, EdgeType.CALLS)

        for ref in ent.references:
            root_id = ref.split(".")[0]
            if self._try_link_asset(
                ent.fqn,
                ref,
                asset_aliases,
                consumed_assets,
                ctx,
                force_render=(root_id in ent.renders),
            ):
                continue
            target = self._resolve_symbol(
                ref,
                parsed,
                ctx,
                local_aliases,
                shadowed=ent.local_names,
                owner_classes=owner_classes,
            )
            if (
                target
                and target != ent.fqn
                and ctx.graph.has_node(target)
                and ctx.graph.nodes[target].get("type") == NodeType.VARIABLE.value
            ):
                ctx.add_edge_fn(ent.fqn, target, EdgeType.REFERENCES)

    def _try_link_asset(
        self,
        source_fqn: str,
        raw_name: str,
        asset_aliases: dict[str, tuple[str, str | None]],
        consumed_assets: set[str],
        ctx: LinkContext,
        *,
        force_render: bool,
    ) -> bool:
        root_id = raw_name.split(".")[0]
        if root_id not in asset_aliases:
            return False
        asset_id, qflag = asset_aliases[root_id]
        etype = EdgeType.RENDERS if (force_render or qflag == "?react") else EdgeType.REFERENCES
        ctx.add_edge_fn(source_fqn, asset_id, etype)
        consumed_assets.add(root_id)
        return True

    def _resolve_target_export(
        self, target_file_rel: str, imp_spec: ImportSpec, ctx: LinkContext
    ) -> str | None:
        resolved = self._resolve_export_recursive(target_file_rel, imp_spec, set(), ctx)
        return resolved or target_file_rel

    def _resolve_export_recursive(
        self,
        target_file_rel: str,
        imp_spec: ImportSpec,
        visited: set[tuple[str, str]],
        ctx: LinkContext,
    ) -> str | None:
        target_parsed = ctx.index.parsed_files.get(target_file_rel)
        if not target_parsed:
            return None

        lookup_key = (
            "__ns__"
            if imp_spec.is_namespace
            else ("default" if imp_spec.is_default else (imp_spec.imported_symbol or "__mod__"))
        )
        state = (target_file_rel, lookup_key)
        if state in visited:
            return None
        visited.add(state)

        if imp_spec.is_namespace:
            return target_parsed.module_fqn
        if imp_spec.is_default:
            return self._resolve_default_export(
                target_file_rel, target_parsed, imp_spec, visited, ctx
            )
        if not imp_spec.imported_symbol:
            return None
        return self._resolve_named_export(
            target_file_rel,
            target_parsed,
            imp_spec.imported_symbol,
            imp_spec.local_name,
            visited,
            ctx,
        )

    def _resolve_default_export(
        self,
        target_file_rel: str,
        target_parsed: FileParseResult,
        imp_spec: ImportSpec,
        visited: set[tuple[str, str]],
        ctx: LinkContext,
    ) -> str | None:
        sym_table = ctx.index.get_symbol_table(_FAMILY)
        default_alias = target_parsed.export_aliases.get("default")
        if default_alias:
            cand = f"{target_parsed.module_fqn}.{default_alias}"
            if cand in sym_table:
                return sym_table[cand]
            forwarded = self._resolve_reexported_import(
                target_file_rel, target_parsed, default_alias, visited, ctx
            )
            if forwarded:
                return forwarded

        for rx in target_parsed.reexports:
            if rx.exported_name == "default":
                forwarded = self._follow_reexport(
                    target_file_rel, rx, imp_spec.local_name, visited, ctx
                )
                if forwarded:
                    return forwarded

        stem = Path(target_file_rel).stem
        for ent in target_parsed.entities:
            if ent.name == stem or ent.is_exported:
                return ent.fqn
        if target_parsed.entities:
            return target_parsed.entities[0].fqn
        return None

    def _resolve_named_export(
        self,
        target_file_rel: str,
        target_parsed: FileParseResult,
        req_name: str,
        local_alias: str | None,
        visited: set[tuple[str, str]],
        ctx: LinkContext,
    ) -> str | None:
        sym_table = ctx.index.get_symbol_table(_FAMILY)
        local_name = target_parsed.export_aliases.get(req_name, req_name)
        cand = f"{target_parsed.module_fqn}.{local_name}"
        if cand in sym_table:
            return sym_table[cand]

        for rx in target_parsed.reexports:
            if rx.exported_name == req_name:
                forwarded = self._follow_reexport(
                    target_file_rel, rx, local_alias, visited, ctx
                )
                if forwarded:
                    return forwarded

        if local_name in target_parsed.exported_names or req_name in target_parsed.export_aliases:
            forwarded = self._resolve_reexported_import(
                target_file_rel, target_parsed, local_name, visited, ctx
            )
            if forwarded:
                return forwarded

        for rx in target_parsed.reexports:
            if not rx.is_star:
                continue
            sub_path, _ = ctx.resolver.resolve(target_file_rel, rx.raw_specifier)
            if not sub_path or sub_path not in ctx.index.parsed_files:
                continue
            probe = ImportSpec(
                local_name=local_alias,
                raw_specifier=rx.raw_specifier,
                imported_symbol=req_name,
            )
            forwarded = self._resolve_export_recursive(sub_path, probe, visited, ctx)
            if forwarded:
                return forwarded
        return None

    def _resolve_reexported_import(
        self,
        target_file_rel: str,
        target_parsed: FileParseResult,
        local_name: str,
        visited: set[tuple[str, str]],
        ctx: LinkContext,
    ) -> str | None:
        for imp in target_parsed.raw_import_specs:
            if imp.local_name != local_name:
                continue
            sub_path, _ = ctx.resolver.resolve(target_file_rel, imp.raw_specifier)
            if sub_path and sub_path in ctx.index.parsed_files:
                return self._resolve_export_recursive(sub_path, imp, visited, ctx)
        return None

    def _follow_reexport(
        self,
        from_file: str,
        rx: ReexportSpec,
        local_alias: str | None,
        visited: set[tuple[str, str]],
        ctx: LinkContext,
    ) -> str | None:
        sub_path, _ = ctx.resolver.resolve(from_file, rx.raw_specifier)
        if not sub_path or sub_path not in ctx.index.parsed_files:
            return None
        if rx.is_namespace:
            return ctx.index.parsed_files[sub_path].module_fqn
        src_sym = rx.source_symbol
        sub_spec = ImportSpec(
            local_name=local_alias,
            raw_specifier=rx.raw_specifier,
            imported_symbol=None if src_sym == "default" else src_sym,
            is_default=(src_sym == "default"),
        )
        return self._resolve_export_recursive(sub_path, sub_spec, visited, ctx)

    def _resolve_local_alias(
        self, clean: str, local_aliases: dict[str, str], ctx: LinkContext
    ) -> str | None:
        if clean in local_aliases:
            return local_aliases[clean]
        if "." not in clean:
            return None
        head, tail = clean.split(".", 1)
        if head not in local_aliases:
            return None
        sym_table = ctx.index.get_symbol_table(_FAMILY)
        head_target = local_aliases[head]
        candidate = f"{head_target}.{tail}"
        if candidate in sym_table:
            return sym_table[candidate]

        node_data = ctx.graph.nodes.get(head_target)
        if not node_data or node_data.get("type") != NodeType.MODULE.value:
            return None
        fpath = node_data.get("file_path")
        if not fpath:
            return None
        member, *rest = tail.split(".", 1)
        resolved_member = self._resolve_export_recursive(
            fpath,
            ImportSpec(local_name=head, raw_specifier="", imported_symbol=member),
            set(),
            ctx,
        )
        if not resolved_member:
            return None
        if not rest:
            return resolved_member
        return sym_table.get(f"{resolved_member}.{rest[0]}")

    def _resolve_injected_member(
        self,
        clean: str,
        parsed: FileParseResult,
        ctx: LinkContext,
        local_aliases: dict[str, str] | None,
        hint_classes: list[str],
    ) -> str | None:
        method_name = clean.split(".")[-1]
        sym_table = ctx.index.get_symbol_table(_FAMILY)
        for hcls in hint_classes:
            cls_target = self._resolve_symbol(hcls, parsed, ctx, local_aliases)
            if cls_target:
                cand = f"{cls_target}.{method_name}"
                if cand in sym_table:
                    return cand
        return None

    def _resolve_owner_member(
        self,
        clean: str,
        parsed: FileParseResult,
        ctx: LinkContext,
        local_aliases: dict[str, str] | None,
        owner_classes: list[str],
    ) -> str | None:
        sym_table = ctx.index.get_symbol_table(_FAMILY)
        seen: set[str] = set()
        queue: list[str] = []
        for owner in owner_classes:
            cls_target = sym_table.get(owner) or self._resolve_symbol(
                owner, parsed, ctx, local_aliases
            )
            if cls_target and cls_target not in seen:
                seen.add(cls_target)
                queue.append(cls_target)

        while queue:
            curr = queue.pop(0)
            cand = f"{curr}.{clean}"
            if cand in sym_table:
                return sym_table[cand]
            if ctx.graph.has_node(curr):
                for _, base, edata in ctx.graph.out_edges(curr, data=True):
                    if edata.get("type") == EdgeType.INHERITS.value and str(base) not in seen:
                        seen.add(str(base))
                        queue.append(str(base))
        return None

    def _resolve_symbol(
        self,
        raw_name: str,
        parsed: FileParseResult,
        ctx: LinkContext,
        local_aliases: dict[str, str] | None = None,
        hint_classes: list[str] | None = None,
        shadowed: set[str] | None = None,
        owner_classes: list[str] | None = None,
    ) -> str | None:
        is_member = raw_name.startswith(("self.", "cls.", "this."))
        clean = raw_name.removeprefix("self.").removeprefix("cls.").removeprefix("this.")

        if shadowed and not is_member and clean.split(".", 1)[0] in shadowed:
            return None

        if local_aliases:
            alias_hit = self._resolve_local_alias(clean, local_aliases, ctx)
            if alias_hit:
                return alias_hit

        sym_table = ctx.index.get_symbol_table(_FAMILY)
        if is_member and "." not in clean:
            if owner_classes:
                return self._resolve_owner_member(clean, parsed, ctx, local_aliases, owner_classes)
            return None

        local_candidate = f"{parsed.module_fqn}.{clean}"
        if local_candidate in sym_table:
            return sym_table[local_candidate]

        if clean in parsed.imports:
            imp_target = parsed.imports[clean]
            if imp_target in sym_table:
                return sym_table[imp_target]

        if clean in sym_table:
            return sym_table[clean]

        if hint_classes and "." in clean:
            injected_hit = self._resolve_injected_member(
                clean, parsed, ctx, local_aliases, hint_classes
            )
            if injected_hit:
                return injected_hit

        bare_js = "." not in clean and not is_member
        caller_is_test = ctx.is_test_path_fn(parsed.file_path)

        suffix_matches = self._filter_global_candidates(
            ctx.index.get_suffix_matches(clean, family=_FAMILY), ctx, caller_is_test, bare_js
        )
        if len(suffix_matches) == 1:
            return suffix_matches[0]

        if "." not in clean and not is_member:
            matches = self._filter_global_candidates(
                ctx.index.get_short_names(clean, family=_FAMILY), ctx, caller_is_test, bare_js
            )
            if len(matches) == 1:
                return matches[0]
        return None

    def _filter_global_candidates(
        self,
        candidates: set[str] | None,
        ctx: LinkContext,
        caller_is_test: bool,
        require_export: bool,
    ) -> list[str]:
        if not candidates:
            return []
        kept: list[str] = []
        for fqn in candidates:
            data = ctx.graph.nodes.get(fqn)
            if data is None or data.get("type") in _NON_CODE_TYPES:
                continue
            fpath = data.get("file_path") or ""
            if not caller_is_test and ctx.is_test_path_fn(fpath):
                continue
            if require_export and not data.get("is_exported", False):
                continue
            kept.append(fqn)
        return kept


# js/ts/web language plugin
class JsTsWebLanguagePlugin(LanguagePlugin):
    """JS, TS, Vue, Svelte plugin."""

    language_id = _FAMILY
    language_family = _FAMILY
    extensions = frozenset({
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".mts",
        ".cts",
        ".tsx",
        ".vue",
        ".svelte",
    })
    ignore_dirs = frozenset({"node_modules", ".next", ".nuxt", "dist", "build", "coverage"})
    config_watch_files = frozenset({"tsconfig.json", "jsconfig.json"})

    def __init__(self) -> None:
        self._parsers: dict[str, tree_sitter.Parser] | None = None

    def is_available(self) -> bool:
        """Check JS/TS Tree-sitter availability."""
        try:
            import tree_sitter_javascript  # noqa: F401
            import tree_sitter_typescript  # noqa: F401
        except ImportError:
            return False
        return True

    def is_test_path(self, rel_path: str) -> bool:
        """Match JS/TS test file paths."""
        return _is_test_path(rel_path)

    def should_index(self, fpath: Path, root_path: Path, rel_path: str) -> bool:
        """Skip emitted JS, minified, vendor files."""
        if _has_ts_sibling(fpath):
            return False
        if is_minified_js(fpath):
            return False
        return not is_vendor_js_path(rel_path)

    def on_file_removed(self, fpath: Path, root_path: Path) -> list[Path]:
        """Recover fallback JS sibling after TS removal."""
        if fpath.exists():
            return []
        for js_ext in _JS_FALLBACK_SUFFIXES.get(fpath.suffix.lower(), ()):
            cand = fpath.with_suffix(js_ext)
            if cand.exists() and cand.is_file():
                try:
                    rel_cand = cand.resolve().relative_to(root_path.resolve()).as_posix()
                except (OSError, ValueError):
                    continue
                if self.should_index(cand, root_path, rel_cand):
                    return [cand]
        return []

    def _get_parsers(self) -> dict[str, tree_sitter.Parser]:
        if self._parsers is None:
            import tree_sitter_javascript as tsjavascript
            import tree_sitter_typescript as tstypescript

            js_lang = tree_sitter.Language(tsjavascript.language())
            ts_lang = tree_sitter.Language(tstypescript.language_typescript())
            tsx_lang = tree_sitter.Language(tstypescript.language_tsx())
            langs = {
                ".js": js_lang,
                ".jsx": js_lang,
                ".mjs": js_lang,
                ".cjs": js_lang,
                ".ts": ts_lang,
                ".mts": ts_lang,
                ".cts": ts_lang,
                ".tsx": tsx_lang,
            }
            self._parsers = {ext: tree_sitter.Parser(lang) for ext, lang in langs.items()}
        return self._parsers

    def parse_file(
        self, source_bytes: bytes, rel_path: str, root_path: Path
    ) -> FileParseResult | None:
        """Parse JS/TS/SFC source into IR."""
        ext = Path(rel_path).suffix.lower()
        if ext not in self.extensions:
            return None

        module_fqn = _path_to_js_module(rel_path)
        parsers = self._get_parsers()

        if ext in _SFC_EXTENSIONS:
            result = parse_sfc(source_bytes, rel_path, module_fqn, ext, parsers)
        else:
            tree = parsers[ext].parse(source_bytes)
            lang_name = "typescript" if "ts" in ext else "javascript"
            result = FileParseResult(
                file_path=rel_path,
                module_fqn=module_fqn,
                language=lang_name,
            )
            extract_ts_js(tree.root_node, source_bytes, module_fqn, result)

        result.language_family = _FAMILY
        result.namespace_fqn = module_fqn
        for ent in result.entities:
            ent.language_family = _FAMILY
            if not ent.namespace_fqn:
                ent.namespace_fqn = module_fqn
        return result

    def create_link_strategy(self, root_path: Path) -> LanguageLinkStrategy:
        """Build JS/TS/Web pass-2 link strategy."""
        return FilePathLinkStrategy()
