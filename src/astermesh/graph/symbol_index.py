from astermesh.graph.model import NodeType
from astermesh.parser.types import FileParseResult

_NON_SHORT_INDEX_TYPES = frozenset({NodeType.CONFIG.value, NodeType.DOC.value})

_LANGUAGE_FAMILY_MAP: dict[str, str] = {
    "python": "python",
    "satellite": "satellite",
    "java": "java",
    "typescript": "js_ts_web",
    "javascript": "js_ts_web",
    "vue": "js_ts_web",
    "svelte": "js_ts_web",
}


# infer canonical language family
def infer_language_family(language: str, language_family: str = "") -> str:
    if language_family:
        return language_family
    return _LANGUAGE_FAMILY_MAP.get(language, language or "unknown")


class SymbolIndex:
    """In-memory symbol lookup tables partitioned by language family."""

    def __init__(self) -> None:
        self.symbol_table: dict[str, str] = {}
        self.short_names: dict[str, set[str]] = {}
        self.suffix_table: dict[str, set[str]] = {}
        self.parsed_files: dict[str, FileParseResult] = {}
        self.file_nodes: dict[str, set[str]] = {}

        self.family_symbol_tables: dict[str, dict[str, str]] = {}
        self.family_short_names: dict[str, dict[str, set[str]]] = {}
        self.family_suffix_tables: dict[str, dict[str, str | set[str]]] = {}
        self.namespace_members: dict[tuple[str, str], dict[str, set[str]]] = {}
        self.overload_groups: dict[tuple[str, str], list[str]] = {}
        self.type_hierarchy: dict[str, set[str]] = {}
        self.type_aliases: dict[str, dict[str, str]] = {}
        self._file_keys: dict[str, bool] = {}

    def clear(self) -> None:
        """Reset all symbol lookup tables."""
        self.symbol_table.clear()
        self.short_names.clear()
        self.suffix_table.clear()
        self.parsed_files.clear()
        self.file_nodes.clear()
        self.family_symbol_tables.clear()
        self.family_short_names.clear()
        self.family_suffix_tables.clear()
        self.namespace_members.clear()
        self.overload_groups.clear()
        self.type_hierarchy.clear()
        self.type_aliases.clear()
        self._file_keys.clear()

    def register_suffixes(self, fqn: str) -> None:
        """Index dot-separated FQN suffixes."""
        parts = fqn.split(".")
        for i in range(len(parts)):
            suffix = ".".join(parts[i:])
            self.suffix_table.setdefault(suffix, set()).add(fqn)

    def unregister_suffixes(self, fqn: str) -> None:
        """Remove dot-separated FQN suffixes."""
        parts = fqn.split(".")
        for i in range(len(parts)):
            suffix = ".".join(parts[i:])
            if suffix in self.suffix_table:
                self.suffix_table[suffix].discard(fqn)

    def register_file(
        self, rel_file: str, parsed: FileParseResult, created_nodes: set[str]
    ) -> None:
        """Register parsed file symbols and indices."""
        if rel_file in self._file_keys:
            self.unregister_file(rel_file)

        file_family = infer_language_family(parsed.language, parsed.language_family)

        self.parsed_files[rel_file] = parsed
        self.symbol_table[parsed.module_fqn] = rel_file
        fam_sym = self.family_symbol_tables.setdefault(file_family, {})
        fam_sym[parsed.module_fqn] = rel_file

        if parsed.type_aliases:
            self.type_aliases.setdefault(file_family, {}).update(parsed.type_aliases)

        for ent in parsed.entities:
            ent_family = ent.language_family or file_family
            self.symbol_table[ent.fqn] = ent.fqn
            self.family_symbol_tables.setdefault(ent_family, {})[ent.fqn] = ent.fqn

            if ent.kind not in _NON_SHORT_INDEX_TYPES:
                self.short_names.setdefault(ent.name, set()).add(ent.fqn)
                self.family_short_names.setdefault(ent_family, {}).setdefault(
                    ent.name, set()
                ).add(ent.fqn)

            if ent_family != "java":
                self.register_suffixes(ent.fqn)
            fam_suffixes = self.family_suffix_tables.setdefault(ent_family, {})
            parts = ent.fqn.split(".")
            for i in range(len(parts)):
                suffix = ".".join(parts[i:])
                existing = fam_suffixes.get(suffix)
                if existing is None:
                    fam_suffixes[suffix] = ent.fqn
                elif isinstance(existing, str):
                    if existing != ent.fqn:
                        fam_suffixes[suffix] = {existing, ent.fqn}
                else:
                    existing.add(ent.fqn)

            ns = ent.namespace_fqn or (
                parsed.namespace_fqn if ent.parent_fqn in (None, parsed.module_fqn) else ""
            )
            if ns and ent.kind not in _NON_SHORT_INDEX_TYPES:
                ns_key = (ent_family, ns)
                self.namespace_members.setdefault(ns_key, {}).setdefault(
                    ent.name, set()
                ).add(ent.fqn)

            if ent.parent_fqn and (ent.is_method or ent.kind == NodeType.FUNCTION.value):
                ov_key = (ent.parent_fqn, ent.name)
                bucket = self.overload_groups.setdefault(ov_key, [])
                if ent.fqn not in bucket:
                    bucket.append(ent.fqn)

            if ent.bases:
                self.type_hierarchy.setdefault(ent.fqn, set()).update(ent.bases)

        self.file_nodes[rel_file] = created_nodes
        self._file_keys[rel_file] = True

    def unregister_file(self, rel_path: str) -> set[str]:
        """Remove file symbols across all indices."""
        nodes_to_remove = self.file_nodes.pop(rel_path, set())
        parsed = self.parsed_files.pop(rel_path, None)
        self._file_keys.pop(rel_path, None)
        if not parsed:
            return nodes_to_remove

        file_family = infer_language_family(parsed.language, parsed.language_family)
        self.symbol_table.pop(parsed.module_fqn, None)
        fam_sym = self.family_symbol_tables.get(file_family)
        if fam_sym is not None:
            fam_sym.pop(parsed.module_fqn, None)
            if not fam_sym:
                del self.family_symbol_tables[file_family]

        if parsed.type_aliases:
            fam_aliases = self.type_aliases.get(file_family)
            if fam_aliases is not None:
                for alias in parsed.type_aliases:
                    fam_aliases.pop(alias, None)
                if not fam_aliases:
                    del self.type_aliases[file_family]

        for ent in parsed.entities:
            ent_family = ent.language_family or file_family
            self.symbol_table.pop(ent.fqn, None)
            ent_sym = self.family_symbol_tables.get(ent_family)
            if ent_sym is not None:
                ent_sym.pop(ent.fqn, None)
                if not ent_sym:
                    del self.family_symbol_tables[ent_family]

            if ent_family != "java":
                self.unregister_suffixes(ent.fqn)

            if ent.name in self.short_names:
                self.short_names[ent.name].discard(ent.fqn)
            fam_short = self.family_short_names.get(ent_family)
            if fam_short is not None and ent.name in fam_short:
                fam_short[ent.name].discard(ent.fqn)
                if not fam_short[ent.name]:
                    del fam_short[ent.name]
                if not fam_short:
                    del self.family_short_names[ent_family]

            fam_suf = self.family_suffix_tables.get(ent_family)
            if fam_suf is not None:
                parts = ent.fqn.split(".")
                for i in range(len(parts)):
                    suffix = ".".join(parts[i:])
                    val = fam_suf.get(suffix)
                    if val is None:
                        continue
                    if isinstance(val, str):
                        if val == ent.fqn:
                            del fam_suf[suffix]
                    else:
                        val.discard(ent.fqn)
                        if not val:
                            del fam_suf[suffix]
                if not fam_suf:
                    del self.family_suffix_tables[ent_family]

            ns = ent.namespace_fqn or (
                parsed.namespace_fqn if ent.parent_fqn in (None, parsed.module_fqn) else ""
            )
            if ns and ent.kind not in _NON_SHORT_INDEX_TYPES:
                ns_key = (ent_family, ns)
                ns_map = self.namespace_members.get(ns_key)
                if ns_map is not None and ent.name in ns_map:
                    ns_map[ent.name].discard(ent.fqn)
                    if not ns_map[ent.name]:
                        del ns_map[ent.name]
                    if not ns_map:
                        del self.namespace_members[ns_key]

            if ent.parent_fqn and (ent.is_method or ent.kind == NodeType.FUNCTION.value):
                ov_key = (ent.parent_fqn, ent.name)
                bucket = self.overload_groups.get(ov_key)
                if bucket is not None:
                    if ent.fqn in bucket:
                        bucket.remove(ent.fqn)
                    if not bucket:
                        del self.overload_groups[ov_key]

            if ent.bases:
                self.type_hierarchy.pop(ent.fqn, None)

        return nodes_to_remove

    def get_symbol_table(self, family: str | None = None) -> dict[str, str]:
        """Return global or family symbol table."""
        if family is None:
            return self.symbol_table
        return self.family_symbol_tables.get(family, {})

    def get_short_names(self, name: str, family: str | None = None) -> set[str]:
        """Return matching FQNs by short name."""
        if family is None:
            return self.short_names.get(name, set())
        return self.family_short_names.get(family, {}).get(name, set())

    def get_suffix_matches(self, suffix: str, family: str | None = None) -> set[str]:
        """Return matching FQNs by suffix."""
        if family is None:
            return self.suffix_table.get(suffix, set())
        val = self.family_suffix_tables.get(family, {}).get(suffix)
        if val is None:
            return set()
        if isinstance(val, str):
            return {val}
        return val

    def get_namespace_members(self, family: str, ns: str, name: str) -> set[str]:
        """Return FQNs matching namespace member."""
        return self.namespace_members.get((family, ns), {}).get(name, set())

    def get_overloads(self, owner_fqn: str, method_name: str) -> list[str]:
        """Return overload FQNs for owner method."""
        return self.overload_groups.get((owner_fqn, method_name), [])

    def get_type_alias(self, family: str, name: str) -> str | None:
        """Return target FQN for type alias."""
        return self.type_aliases.get(family, {}).get(name)
