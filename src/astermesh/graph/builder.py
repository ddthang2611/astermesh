import os
import threading
from pathlib import Path
import networkx as nx
from astermesh.graph.model import EdgeType, EntityNode, NodeType
from astermesh.parser.engine import ASTEngine, FileParseResult

IGNORE_DIRS = frozenset({
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".agents",
    ".pytest_cache",
    ".mypy_cache",
    "dist",
    "build",
})


def is_ignored_dir(path: Path | str) -> bool:
    parts = Path(path).parts
    return any(p in IGNORE_DIRS or (p.startswith(".") and p not in {".", ".."}) for p in parts)


# in-memory graph management and incremental updater
class GraphBuilder:
    def __init__(self, root_path: Path) -> None:
        self.root_path = root_path.resolve()
        self.graph = nx.MultiDiGraph()
        self.parser = ASTEngine()
        self.lock = threading.RLock()
        self._symbol_table: dict[str, str] = {}
        self._short_names: dict[str, set[str]] = {}
        self._parsed_files: dict[str, FileParseResult] = {}
        self._file_nodes: dict[str, set[str]] = {}

    def _add_edge(self, u: str, v: str, etype: EdgeType) -> None:
        self.graph.add_edge(u, v, key=etype.value, type=etype.value)

    # construct full two-pass graph
    def build_full(self) -> nx.MultiDiGraph:
        with self.lock:
            self.graph.clear()
            self._symbol_table.clear()
            self._short_names.clear()
            self._parsed_files.clear()
            self._file_nodes.clear()

            self._scan_filesystem()
            for rel_file in list(self._parsed_files.keys()):
                self._link_file_relations(self._parsed_files[rel_file])
            return self.graph

    # pass 1: scan directories and register symbols
    def _scan_filesystem(self) -> None:
        root_node = EntityNode(
            id=".",
            name=self.root_path.name,
            node_type=NodeType.FOLDER,
            file_path=".",
        )
        self.graph.add_node(".", **root_node.to_dict())

        for dirpath, dirnames, filenames in os.walk(self.root_path):
            dirnames[:] = [d for d in dirnames if not is_ignored_dir(d)]
            curr_dir = Path(dirpath)
            rel_dir = curr_dir.relative_to(self.root_path).as_posix()

            if rel_dir != ".":
                parent_dir = curr_dir.parent.relative_to(self.root_path).as_posix()
                folder_node = EntityNode(
                    id=rel_dir,
                    name=curr_dir.name,
                    node_type=NodeType.FOLDER,
                    file_path=rel_dir,
                )
                self.graph.add_node(rel_dir, **folder_node.to_dict())
                self._add_edge(parent_dir, rel_dir, EdgeType.CONTAINS)

            for fname in filenames:
                fpath = curr_dir / fname
                if not self.parser.supports(fpath.suffix.lower()):
                    continue
                self._index_single_file(fpath, rel_dir)

    def _index_single_file(self, fpath: Path, parent_dir: str) -> str | None:
        rel_file = fpath.relative_to(self.root_path).as_posix()
        file_node = EntityNode(
            id=rel_file,
            name=fpath.name,
            node_type=NodeType.FILE,
            file_path=rel_file,
        )
        self.graph.add_node(rel_file, **file_node.to_dict())
        self._add_edge(parent_dir, rel_file, EdgeType.CONTAINS)

        parsed = self.parser.parse_file(fpath, self.root_path)
        if not parsed:
            return None

        self._parsed_files[rel_file] = parsed
        self._symbol_table[parsed.module_fqn] = rel_file
        created_nodes: set[str] = {rel_file}

        for ent in parsed.entities:
            ntype = NodeType(ent.kind)
            enode = EntityNode(
                id=ent.fqn,
                name=ent.name,
                node_type=ntype,
                file_path=rel_file,
                line_start=ent.line_start,
                line_end=ent.line_end,
                parameters=ent.parameters,
                is_async=ent.is_async,
                is_method=ent.is_method,
            )
            self.graph.add_node(ent.fqn, **enode.to_dict())
            created_nodes.add(ent.fqn)

            self._symbol_table[ent.fqn] = ent.fqn
            self._short_names.setdefault(ent.name, set()).add(ent.fqn)

            def_source = rel_file if ent.parent_fqn == parsed.module_fqn else ent.parent_fqn
            if def_source and self.graph.has_node(def_source):
                self._add_edge(def_source, ent.fqn, EdgeType.DEFINES)

        self._file_nodes[rel_file] = created_nodes
        return rel_file

    # pass 2: link fqn static relationships
    def _link_file_relations(self, parsed: FileParseResult) -> None:
        rel_file = parsed.file_path
        for alias, target_mod in parsed.imports.items():
            resolved = self._resolve_symbol(target_mod, parsed)
            if resolved and self.graph.has_node(resolved):
                self._add_edge(rel_file, resolved, EdgeType.IMPORTS)

        for ent in parsed.entities:
            for base in ent.bases:
                target = self._resolve_symbol(base, parsed)
                if target and target != ent.fqn and self.graph.has_node(target):
                    self._add_edge(ent.fqn, target, EdgeType.INHERITS)

            for call in ent.calls:
                target = self._resolve_symbol(call, parsed)
                if target and target != ent.fqn and self.graph.has_node(target):
                    self._add_edge(ent.fqn, target, EdgeType.CALLS)

            for ref in ent.references:
                target = self._resolve_symbol(ref, parsed)
                if (
                    target
                    and target != ent.fqn
                    and self.graph.has_node(target)
                    and self.graph.nodes[target].get("type") == NodeType.VARIABLE.value
                ):
                    self._add_edge(ent.fqn, target, EdgeType.REFERENCES)

    def _resolve_symbol(self, raw_name: str, parsed: FileParseResult) -> str | None:
        clean = raw_name.removeprefix("self.").removeprefix("cls.")
        local_candidate = f"{parsed.module_fqn}.{clean}"
        if local_candidate in self._symbol_table:
            return self._symbol_table[local_candidate]

        if clean in parsed.imports:
            imp_target = parsed.imports[clean]
            if imp_target in self._symbol_table:
                return self._symbol_table[imp_target]

        if clean in self._symbol_table:
            return self._symbol_table[clean]

        short = clean.split(".")[-1]
        matches = self._short_names.get(short)
        if matches and len(matches) == 1:
            return next(iter(matches))
        return None

    # incremental update for file modification or deletion
    def update_file(self, fpath: Path) -> None:
        with self.lock:
            rel_file = fpath.resolve().relative_to(self.root_path).as_posix()
            self.remove_path(rel_file)
            if fpath.exists() and fpath.is_file():
                parent_dir = self.ensure_folder(fpath.parent)
                indexed = self._index_single_file(fpath, parent_dir)
                if indexed and indexed in self._parsed_files:
                    self._link_file_relations(self._parsed_files[indexed])

    def remove_path(self, rel_path: str) -> None:
        with self.lock:
            nodes_to_remove = self._file_nodes.pop(rel_path, set())
            parsed = self._parsed_files.pop(rel_path, None)
            if parsed:
                self._symbol_table.pop(parsed.module_fqn, None)
                for ent in parsed.entities:
                    self._symbol_table.pop(ent.fqn, None)
                    if ent.name in self._short_names:
                        self._short_names[ent.name].discard(ent.fqn)

            for nid in nodes_to_remove:
                if self.graph.has_node(nid):
                    self.graph.remove_node(nid)

            if self.graph.has_node(rel_path):
                self.graph.remove_node(rel_path)

    def ensure_folder(self, dir_path: Path) -> str:
        rel_dir = dir_path.resolve().relative_to(self.root_path).as_posix()
        if rel_dir == ".":
            return "."
        parts = rel_dir.split("/")
        curr = "."
        for part in parts:
            nxt = part if curr == "." else f"{curr}/{part}"
            if not self.graph.has_node(nxt):
                fnode = EntityNode(
                    id=nxt, name=part, node_type=NodeType.FOLDER, file_path=nxt
                )
                self.graph.add_node(nxt, **fnode.to_dict())
                self._add_edge(curr, nxt, EdgeType.CONTAINS)
            curr = nxt
        return rel_dir

    _ensure_folder_hierarchy = ensure_folder
