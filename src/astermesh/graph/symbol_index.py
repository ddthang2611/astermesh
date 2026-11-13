from astermesh.graph.model import NodeType
from astermesh.parser.types import FileParseResult

_NON_SHORT_INDEX_TYPES = frozenset({NodeType.CONFIG.value, NodeType.DOC.value})


class SymbolIndex:
    """In-memory symbol lookup tables for FQN, short names, dot-path suffixes, and parsed files."""

    def __init__(self) -> None:
        self.symbol_table: dict[str, str] = {}
        self.short_names: dict[str, set[str]] = {}
        self.suffix_table: dict[str, set[str]] = {}
        self.parsed_files: dict[str, FileParseResult] = {}
        self.file_nodes: dict[str, set[str]] = {}

    def clear(self) -> None:
        """Reset all symbol lookup tables."""
        self.symbol_table.clear()
        self.short_names.clear()
        self.suffix_table.clear()
        self.parsed_files.clear()
        self.file_nodes.clear()

    def register_suffixes(self, fqn: str) -> None:
        """Index every dot-separated suffix of an FQN for O(1) suffix lookup."""
        parts = fqn.split(".")
        for i in range(len(parts)):
            suffix = ".".join(parts[i:])
            self.suffix_table.setdefault(suffix, set()).add(fqn)

    def unregister_suffixes(self, fqn: str) -> None:
        """Remove all dot-separated suffixes of an FQN from the suffix index."""
        parts = fqn.split(".")
        for i in range(len(parts)):
            suffix = ".".join(parts[i:])
            if suffix in self.suffix_table:
                self.suffix_table[suffix].discard(fqn)

    def register_file(
        self, rel_file: str, parsed: FileParseResult, created_nodes: set[str]
    ) -> None:
        """Register a parsed file's module, entities, suffixes, and created graph node IDs."""
        self.parsed_files[rel_file] = parsed
        self.symbol_table[parsed.module_fqn] = rel_file
        for ent in parsed.entities:
            self.symbol_table[ent.fqn] = ent.fqn
            if ent.kind not in _NON_SHORT_INDEX_TYPES:
                self.short_names.setdefault(ent.name, set()).add(ent.fqn)
            self.register_suffixes(ent.fqn)
        self.file_nodes[rel_file] = created_nodes

    def unregister_file(self, rel_path: str) -> set[str]:
        """Remove all symbols and suffix entries belonging to a file and return its node IDs."""
        nodes_to_remove = self.file_nodes.pop(rel_path, set())
        parsed = self.parsed_files.pop(rel_path, None)
        if parsed:
            self.symbol_table.pop(parsed.module_fqn, None)
            for ent in parsed.entities:
                self.symbol_table.pop(ent.fqn, None)
                self.unregister_suffixes(ent.fqn)
                if ent.name in self.short_names:
                    self.short_names[ent.name].discard(ent.fqn)
        return nodes_to_remove
