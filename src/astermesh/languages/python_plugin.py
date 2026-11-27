from pathlib import Path

import tree_sitter

from astermesh.graph.filters import is_test_path as _is_test_path
from astermesh.graph.model import EdgeType, NodeType
from astermesh.languages.base import LanguageLinkStrategy, LanguagePlugin, LinkContext
from astermesh.parser.extractors.python import extract_python
from astermesh.parser.types import FileParseResult, RawEntity

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


def _path_to_python_module(rel_path: str) -> str:
    parts = list(Path(rel_path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else "root"


# python pass-2 linking strategy
class DottedModuleLinkStrategy(LanguageLinkStrategy):
    """Link Python imports, calls, and inheritance."""

    def link_file(self, parsed: FileParseResult, ctx: LinkContext) -> None:
        """Link Python relations for one file."""
        local_aliases = self._link_imports(parsed, ctx)
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
            self._link_entity(ent, parsed, ctx, local_aliases, extra_classes, owner_classes)

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

    def _link_imports(self, parsed: FileParseResult, ctx: LinkContext) -> dict[str, str]:
        local_aliases: dict[str, str] = {}
        for alias, target_mod in parsed.imports.items():
            resolved = self._resolve_symbol(target_mod, parsed, ctx)
            if resolved and ctx.graph.has_node(resolved):
                ctx.add_edge_fn(parsed.file_path, resolved, EdgeType.IMPORTS)
                local_aliases[alias] = resolved
        return local_aliases

    def _link_entity(
        self,
        ent: RawEntity,
        parsed: FileParseResult,
        ctx: LinkContext,
        local_aliases: dict[str, str],
        extra_classes: list[str],
        owner_classes: list[str],
    ) -> None:
        for names, etype in (
            (ent.injects, EdgeType.INJECTS),
            (ent.provides, EdgeType.PROVIDES),
            (ent.bases, EdgeType.INHERITS),
        ):
            for name in names:
                target = self._resolve_symbol(name, parsed, ctx, local_aliases)
                ctx.add_entity_edge_fn(ent.fqn, target, etype)

        for call in ent.calls:
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
        sym_table = ctx.index.get_symbol_table("python")
        candidate = f"{local_aliases[head]}.{tail}"
        return sym_table.get(candidate)

    def _resolve_injected_member(
        self,
        clean: str,
        parsed: FileParseResult,
        ctx: LinkContext,
        local_aliases: dict[str, str] | None,
        hint_classes: list[str],
    ) -> str | None:
        method_name = clean.split(".")[-1]
        sym_table = ctx.index.get_symbol_table("python")
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
        sym_table = ctx.index.get_symbol_table("python")
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
        is_member = raw_name.startswith(("self.", "cls."))
        clean = raw_name.removeprefix("self.").removeprefix("cls.")

        if shadowed and not is_member and clean.split(".", 1)[0] in shadowed:
            return None

        if local_aliases:
            alias_hit = self._resolve_local_alias(clean, local_aliases, ctx)
            if alias_hit:
                return alias_hit

        sym_table = ctx.index.get_symbol_table("python")
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

        caller_is_test = ctx.is_test_path_fn(parsed.file_path)
        suffix_matches = self._filter_global_candidates(
            ctx.index.get_suffix_matches(clean, family="python"), ctx, caller_is_test
        )
        if len(suffix_matches) == 1:
            return suffix_matches[0]

        if "." not in clean and not is_member:
            matches = self._filter_global_candidates(
                ctx.index.get_short_names(clean, family="python"), ctx, caller_is_test
            )
            if len(matches) == 1:
                return matches[0]
        return None

    def _filter_global_candidates(
        self, candidates: set[str] | None, ctx: LinkContext, caller_is_test: bool
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
            kept.append(fqn)
        return kept


# python language plugin
class PythonLanguagePlugin(LanguagePlugin):
    """Python Tree-sitter language plugin."""

    language_id = "python"
    language_family = "python"
    extensions = frozenset({".py"})
    ignore_dirs = frozenset()
    config_watch_files = frozenset()

    def __init__(self) -> None:
        self._parser: tree_sitter.Parser | None = None

    def is_available(self) -> bool:
        """Check Python Tree-sitter availability."""
        try:
            import tree_sitter_python  # noqa: F401
        except ImportError:
            return False
        return True

    def is_test_path(self, rel_path: str) -> bool:
        """Match Python test file paths."""
        return _is_test_path(rel_path)

    def _get_parser(self) -> tree_sitter.Parser:
        if self._parser is None:
            import tree_sitter_python as tspython

            self._parser = tree_sitter.Parser(tree_sitter.Language(tspython.language()))
        return self._parser

    def parse_file(
        self, source_bytes: bytes, rel_path: str, root_path: Path
    ) -> FileParseResult | None:
        """Parse Python source into IR."""
        module_fqn = _path_to_python_module(rel_path)
        tree = self._get_parser().parse(source_bytes)
        result = FileParseResult(
            file_path=rel_path,
            module_fqn=module_fqn,
            language="python",
            language_family=self.language_family,
            namespace_fqn=module_fqn,
        )
        extract_python(tree.root_node, source_bytes, module_fqn, result)
        result.language_family = self.language_family
        result.namespace_fqn = module_fqn
        for ent in result.entities:
            ent.language_family = self.language_family
            if not ent.namespace_fqn:
                ent.namespace_fqn = module_fqn
        return result

    def create_link_strategy(self, root_path: Path) -> LanguageLinkStrategy:
        """Build Python pass-2 link strategy."""
        return DottedModuleLinkStrategy()
