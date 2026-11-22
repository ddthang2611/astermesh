from collections.abc import Callable
from pathlib import Path

import networkx as nx

from astermesh.graph.filters import is_test_path
from astermesh.graph.model import EdgeType, NodeType
from astermesh.graph.symbol_index import SymbolIndex
from astermesh.parser.resolver import ModuleResolver, is_asset_path
from astermesh.parser.types import FileParseResult, ImportSpec, RawEntity, ReexportSpec

_JS_LANGS = frozenset({"typescript", "javascript", "vue", "svelte"})
_NON_CODE_TYPES = frozenset({
    NodeType.CONFIG.value,
    NodeType.DOC.value,
    NodeType.FOLDER.value,
    NodeType.FILE.value,
    NodeType.ASSET.value,
})


class RelationLinker:
    """Pass-2 linker resolving imports, calls, renders, DI, and references into graph edges."""

    def __init__(
        self,
        graph: nx.MultiDiGraph,
        index: SymbolIndex,
        resolver: ModuleResolver,
        ensure_asset_fn: Callable[[str], str],
    ) -> None:
        self.graph = graph
        self.index = index
        self.resolver = resolver
        self._ensure_asset_node = ensure_asset_fn

    def _add_edge(self, u: str, v: str, etype: EdgeType) -> None:
        self.graph.add_edge(u, v, key=etype.value, type=etype.value)

    def link_file(self, parsed: FileParseResult) -> None:
        """Link all imports and entity-level relations for a single parsed file."""
        rel_file = parsed.file_path
        local_aliases, asset_aliases = self._link_imports(parsed)
        consumed_assets: set[str] = set()

        class_types = {
            NodeType.CLASS.value,
            NodeType.SERVICE.value,
            NodeType.COMPONENT.value,
        }
        class_injects: dict[str, list[str]] = {
            ent.fqn: ent.injects for ent in parsed.entities if ent.injects
        }
        class_owners: dict[str, list[str]] = {
            ent.fqn: [ent.fqn, *ent.bases]
            for ent in parsed.entities
            if ent.kind in class_types
        }

        for ent in parsed.entities:
            extra_classes = self._lookup_enclosing(ent.parent_fqn, class_injects)
            owner_classes = self._lookup_enclosing(ent.parent_fqn, class_owners)
            self._link_entity_relations(
                ent,
                parsed,
                local_aliases,
                asset_aliases,
                extra_classes,
                consumed_assets,
                owner_classes=owner_classes,
            )

        # fallback file-level edge for unused asset imports
        for alias_name, (asset_id, _) in asset_aliases.items():
            if alias_name not in consumed_assets:
                self._add_edge(rel_file, asset_id, EdgeType.IMPORTS)

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
        self, parsed: FileParseResult
    ) -> tuple[dict[str, str], dict[str, tuple[str, str | None]]]:
        rel_file = parsed.file_path
        local_aliases: dict[str, str] = {}
        asset_aliases: dict[str, tuple[str, str | None]] = {}

        for alias, target_mod in parsed.imports.items():
            resolved = self.resolve_symbol(target_mod, parsed)
            if resolved and self.graph.has_node(resolved):
                self._add_edge(rel_file, resolved, EdgeType.IMPORTS)
                local_aliases[alias] = resolved

        for imp in parsed.raw_import_specs:
            resolved_path, query_flag = self.resolver.resolve(rel_file, imp.raw_specifier)
            if not resolved_path:
                continue

            if resolved_path not in self.index.parsed_files and is_asset_path(resolved_path):
                asset_id = self._ensure_asset_node(resolved_path)
                if imp.local_name:
                    asset_aliases[imp.local_name] = (asset_id, query_flag)
                else:
                    self._add_edge(rel_file, asset_id, EdgeType.IMPORTS)
                continue

            if self.graph.has_node(resolved_path):
                self._add_edge(rel_file, resolved_path, EdgeType.IMPORTS)
                if imp.local_name:
                    target_fqn = self._resolve_target_export(resolved_path, imp)
                    if target_fqn:
                        local_aliases[imp.local_name] = target_fqn

        return local_aliases, asset_aliases

    def _link_entity_relations(
        self,
        ent: RawEntity,
        parsed: FileParseResult,
        local_aliases: dict[str, str],
        asset_aliases: dict[str, tuple[str, str | None]],
        extra_classes: list[str],
        consumed_assets: set[str],
        owner_classes: list[str] | None = None,
    ) -> None:
        for rendered in ent.renders:
            if self._try_link_asset(
                ent.fqn, rendered, asset_aliases, consumed_assets, force_render=True
            ):
                continue
            target = self.resolve_symbol(
                rendered,
                parsed,
                local_aliases,
                shadowed=ent.local_names,
                owner_classes=owner_classes,
            )
            self._add_entity_edge(ent.fqn, target, EdgeType.RENDERS)

        for names, etype in (
            (ent.injects, EdgeType.INJECTS),
            (ent.provides, EdgeType.PROVIDES),
            (ent.bases, EdgeType.INHERITS),
        ):
            for name in names:
                target = self.resolve_symbol(name, parsed, local_aliases)
                self._add_entity_edge(ent.fqn, target, etype)

        for call in ent.calls:
            if self._try_link_asset(
                ent.fqn, call, asset_aliases, consumed_assets, force_render=False
            ):
                continue
            target = self.resolve_symbol(
                call,
                parsed,
                local_aliases,
                hint_classes=extra_classes,
                shadowed=ent.local_names,
                owner_classes=owner_classes,
            )
            self._add_entity_edge(ent.fqn, target, EdgeType.CALLS)

        for ref in ent.references:
            root_id = ref.split(".")[0]
            if self._try_link_asset(
                ent.fqn,
                ref,
                asset_aliases,
                consumed_assets,
                force_render=(root_id in ent.renders),
            ):
                continue
            target = self.resolve_symbol(
                ref,
                parsed,
                local_aliases,
                shadowed=ent.local_names,
                owner_classes=owner_classes,
            )
            if (
                target
                and target != ent.fqn
                and self.graph.has_node(target)
                and self.graph.nodes[target].get("type") == NodeType.VARIABLE.value
            ):
                self._add_edge(ent.fqn, target, EdgeType.REFERENCES)

    def _try_link_asset(
        self,
        source_fqn: str,
        raw_name: str,
        asset_aliases: dict[str, tuple[str, str | None]],
        consumed_assets: set[str],
        *,
        force_render: bool,
    ) -> bool:
        root_id = raw_name.split(".")[0]
        if root_id not in asset_aliases:
            return False
        asset_id, qflag = asset_aliases[root_id]
        etype = EdgeType.RENDERS if (force_render or qflag == "?react") else EdgeType.REFERENCES
        self._add_edge(source_fqn, asset_id, etype)
        consumed_assets.add(root_id)
        return True

    def _add_entity_edge(self, source_fqn: str, target: str | None, etype: EdgeType) -> None:
        if target and target != source_fqn and self.graph.has_node(target):
            self._add_edge(source_fqn, target, etype)

    def _resolve_target_export(self, target_file_rel: str, imp_spec: ImportSpec) -> str | None:
        resolved = self._resolve_export_recursive(target_file_rel, imp_spec, set())
        return resolved or target_file_rel

    def _resolve_export_recursive(
        self,
        target_file_rel: str,
        imp_spec: ImportSpec,
        visited: set[tuple[str, str]],
    ) -> str | None:
        """Resolve an imported binding transitively across barrel re-exports."""
        target_parsed = self.index.parsed_files.get(target_file_rel)
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
            return self._resolve_default_export(target_file_rel, target_parsed, imp_spec, visited)
        if not imp_spec.imported_symbol:
            return None
        return self._resolve_named_export(
            target_file_rel, target_parsed, imp_spec.imported_symbol, imp_spec.local_name, visited
        )

    def _resolve_default_export(
        self,
        target_file_rel: str,
        target_parsed: FileParseResult,
        imp_spec: ImportSpec,
        visited: set[tuple[str, str]],
    ) -> str | None:
        sym_table = self.index.symbol_table
        default_alias = target_parsed.export_aliases.get("default")
        if default_alias:
            cand = f"{target_parsed.module_fqn}.{default_alias}"
            if cand in sym_table:
                return sym_table[cand]
            forwarded = self._resolve_reexported_import(
                target_file_rel, target_parsed, default_alias, visited
            )
            if forwarded:
                return forwarded

        for rx in target_parsed.reexports:
            if rx.exported_name == "default":
                forwarded = self._follow_reexport(
                    target_file_rel, rx, imp_spec.local_name, visited
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
    ) -> str | None:
        sym_table = self.index.symbol_table
        local_name = target_parsed.export_aliases.get(req_name, req_name)
        cand = f"{target_parsed.module_fqn}.{local_name}"
        if cand in sym_table:
            return sym_table[cand]

        for rx in target_parsed.reexports:
            if rx.exported_name == req_name:
                forwarded = self._follow_reexport(target_file_rel, rx, local_alias, visited)
                if forwarded:
                    return forwarded

        if local_name in target_parsed.exported_names or req_name in target_parsed.export_aliases:
            forwarded = self._resolve_reexported_import(
                target_file_rel, target_parsed, local_name, visited
            )
            if forwarded:
                return forwarded

        for rx in target_parsed.reexports:
            if not rx.is_star:
                continue
            sub_path, _ = self.resolver.resolve(target_file_rel, rx.raw_specifier)
            if not sub_path or sub_path not in self.index.parsed_files:
                continue
            probe = ImportSpec(
                local_name=local_alias,
                raw_specifier=rx.raw_specifier,
                imported_symbol=req_name,
            )
            forwarded = self._resolve_export_recursive(sub_path, probe, visited)
            if forwarded:
                return forwarded
        return None

    def _resolve_reexported_import(
        self,
        target_file_rel: str,
        target_parsed: FileParseResult,
        local_name: str,
        visited: set[tuple[str, str]],
    ) -> str | None:
        for imp in target_parsed.raw_import_specs:
            if imp.local_name != local_name:
                continue
            sub_path, _ = self.resolver.resolve(target_file_rel, imp.raw_specifier)
            if sub_path and sub_path in self.index.parsed_files:
                return self._resolve_export_recursive(sub_path, imp, visited)
        return None

    def _follow_reexport(
        self,
        from_file: str,
        rx: ReexportSpec,
        local_alias: str | None,
        visited: set[tuple[str, str]],
    ) -> str | None:
        sub_path, _ = self.resolver.resolve(from_file, rx.raw_specifier)
        if not sub_path or sub_path not in self.index.parsed_files:
            return None
        if rx.is_namespace:
            return self.index.parsed_files[sub_path].module_fqn
        src_sym = rx.source_symbol
        sub_spec = ImportSpec(
            local_name=local_alias,
            raw_specifier=rx.raw_specifier,
            imported_symbol=None if src_sym == "default" else src_sym,
            is_default=(src_sym == "default"),
        )
        return self._resolve_export_recursive(sub_path, sub_spec, visited)

    def _resolve_local_alias(self, clean: str, local_aliases: dict[str, str]) -> str | None:
        if clean in local_aliases:
            return local_aliases[clean]
        if "." not in clean:
            return None
        head, tail = clean.split(".", 1)
        if head not in local_aliases:
            return None
        sym_table = self.index.symbol_table
        head_target = local_aliases[head]
        candidate = f"{head_target}.{tail}"
        if candidate in sym_table:
            return sym_table[candidate]
        # follow namespace imports targeting barrel modules
        node_data = self.graph.nodes.get(head_target)
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
        )
        if not resolved_member:
            return None
        if not rest:
            return resolved_member
        full_cand = f"{resolved_member}.{rest[0]}"
        return sym_table.get(full_cand)

    def _resolve_injected_member(
        self,
        clean: str,
        parsed: FileParseResult,
        local_aliases: dict[str, str] | None,
        hint_classes: list[str],
    ) -> str | None:
        method_name = clean.split(".")[-1]
        sym_table = self.index.symbol_table
        for hcls in hint_classes:
            cls_target = self.resolve_symbol(hcls, parsed, local_aliases)
            if cls_target:
                cand = f"{cls_target}.{method_name}"
                if cand in sym_table:
                    return cand
        return None

    def _resolve_owner_member(
        self,
        clean: str,
        parsed: FileParseResult,
        local_aliases: dict[str, str] | None,
        owner_classes: list[str],
    ) -> str | None:
        sym_table = self.index.symbol_table
        seen: set[str] = set()
        queue: list[str] = []
        for owner in owner_classes:
            cls_target = sym_table.get(owner) or self.resolve_symbol(
                owner, parsed, local_aliases
            )
            if cls_target and cls_target not in seen:
                seen.add(cls_target)
                queue.append(cls_target)
        # walk enclosing class and base classes
        while queue:
            curr = queue.pop(0)
            cand = f"{curr}.{clean}"
            if cand in sym_table:
                return sym_table[cand]
            if self.graph.has_node(curr):
                for _, base, edata in self.graph.out_edges(curr, data=True):
                    if edata.get("type") == EdgeType.INHERITS.value and str(base) not in seen:
                        seen.add(str(base))
                        queue.append(str(base))
        return None

    def resolve_symbol(
        self,
        raw_name: str,
        parsed: FileParseResult,
        local_aliases: dict[str, str] | None = None,
        hint_classes: list[str] | None = None,
        shadowed: set[str] | None = None,
        owner_classes: list[str] | None = None,
    ) -> str | None:
        """Resolve an identifier or dotted path against local scope, imports, and global index."""
        is_member = raw_name.startswith(("self.", "cls.", "this."))
        clean = raw_name.removeprefix("self.").removeprefix("cls.").removeprefix("this.")

        # a name bound inside the caller's own scope never refers to another symbol
        if shadowed and not is_member and clean.split(".", 1)[0] in shadowed:
            return None

        if local_aliases:
            alias_hit = self._resolve_local_alias(clean, local_aliases)
            if alias_hit:
                return alias_hit

        sym_table = self.index.symbol_table
        if is_member and "." not in clean:
            if owner_classes:
                return self._resolve_owner_member(clean, parsed, local_aliases, owner_classes)
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

        # try resolving member call against constructor-injected service types
        if hint_classes and "." in clean:
            injected_hit = self._resolve_injected_member(
                clean, parsed, local_aliases, hint_classes
            )
            if injected_hit:
                return injected_hit

        # global heuristics: only consider symbols that could plausibly be reached
        bare_js = "." not in clean and not is_member and parsed.language in _JS_LANGS
        caller_is_test = is_test_path(parsed.file_path)

        suffix_matches = self._filter_global_candidates(
            self.index.suffix_table.get(clean), caller_is_test, bare_js
        )
        if len(suffix_matches) == 1:
            return suffix_matches[0]

        # restrict short_names lookup to bare calls
        if "." not in clean and not is_member:
            matches = self._filter_global_candidates(
                self.index.short_names.get(clean), caller_is_test, bare_js
            )
            if len(matches) == 1:
                return matches[0]
        return None

    def _filter_global_candidates(
        self, candidates: set[str] | None, caller_is_test: bool, require_export: bool
    ) -> list[str]:
        if not candidates:
            return []
        kept: list[str] = []
        for fqn in candidates:
            data = self.graph.nodes.get(fqn)
            if data is None or data.get("type") in _NON_CODE_TYPES:
                continue
            fpath = data.get("file_path") or ""
            # production code never reaches into test files
            if not caller_is_test and is_test_path(fpath):
                continue
            # es modules: a bare identifier from another file must be exported
            if require_export and not data.get("is_exported", False):
                tparsed = self.index.parsed_files.get(fpath)
                if tparsed is not None and tparsed.language in _JS_LANGS:
                    continue
            kept.append(fqn)
        return kept
