import os
import threading
from pathlib import Path

import networkx as nx

from astermesh.graph.filters import (
    IGNORE_DIRS,
    IGNORED_FILE_SUFFIXES,
    TEST_DATA_DIRS,
    is_ignored_dir,
    is_ignored_file,
    is_test_path,
)
from astermesh.graph.linker import RelationLinker
from astermesh.graph.model import EdgeType, EntityNode, NodeType
from astermesh.graph.symbol_index import SymbolIndex
from astermesh.parser.engine import ASTEngine
from astermesh.parser.resolver import ModuleResolver
from astermesh.parser.satellite import SatelliteParser

__all__ = [
    "IGNORED_FILE_SUFFIXES",
    "IGNORE_DIRS",
    "TEST_DATA_DIRS",
    "GraphBuilder",
    "is_ignored_dir",
    "is_ignored_file",
    "is_test_path",
]


# in-memory graph management and incremental updater
class GraphBuilder:
    def __init__(self, root_path: Path) -> None:
        self.root_path = root_path.resolve()
        self.graph = nx.MultiDiGraph()
        self.parser = ASTEngine()
        self.satellite = SatelliteParser()
        self.resolver = ModuleResolver(self.root_path)
        self.lock = threading.RLock()
        self._index = SymbolIndex()
        self._linker = RelationLinker(
            self.graph, self._index, self.resolver, self._ensure_asset_node
        )

    @property
    def _suffix_table(self) -> dict[str, set[str]]:
        return self._index.suffix_table

    def _add_edge(self, u: str, v: str, etype: EdgeType) -> None:
        self.graph.add_edge(u, v, key=etype.value, type=etype.value)

    # construct full two-pass graph
    def build_full(self) -> nx.MultiDiGraph:
        with self.lock:
            self.graph.clear()
            self._index.clear()
            self.resolver.reload_config()

            self._scan_filesystem()
            self.resolver.set_known_files(set(self._index.parsed_files.keys()))

            for parsed in list(self._index.parsed_files.values()):
                self._linker.link_file(parsed)
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
                if self._is_indexable(fpath):
                    self._index_single_file(fpath, rel_dir)

    def _is_indexable(self, fpath: Path) -> bool:
        if is_ignored_file(fpath.name):
            return False
        if self.parser.supports(fpath.suffix.lower()):
            return True
        if not self.satellite.supports(fpath):
            return False
        # json/yaml fixtures under test folders flood the graph with config nodes
        try:
            rel = fpath.resolve().relative_to(self.root_path).as_posix()
        except ValueError:
            return False
        return not is_test_path(rel)

    def _index_single_file(self, fpath: Path, parent_dir: str) -> str | None:
        rel_file = fpath.relative_to(self.root_path).as_posix()
        parsed = (
            self.parser.parse_file(fpath, self.root_path)
            if self.parser.supports(fpath.suffix.lower())
            else self.satellite.parse_file(fpath, self.root_path)
        )
        if not parsed:
            return None

        file_node = EntityNode(
            id=rel_file,
            name=fpath.name,
            node_type=NodeType.FILE,
            file_path=rel_file,
        )
        self.graph.add_node(rel_file, **file_node.to_dict())
        self._add_edge(parent_dir, rel_file, EdgeType.CONTAINS)

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
                is_exported=ent.is_exported,
            )
            self.graph.add_node(ent.fqn, **enode.to_dict())
            created_nodes.add(ent.fqn)

            def_source = rel_file if ent.parent_fqn == parsed.module_fqn else ent.parent_fqn
            if def_source and self.graph.has_node(def_source):
                self._add_edge(def_source, ent.fqn, EdgeType.DEFINES)

        self._index.register_file(rel_file, parsed, created_nodes)
        return rel_file

    def _ensure_asset_node(self, rel_asset: str) -> str:
        if self.graph.has_node(rel_asset):
            return rel_asset
        asset_path = self.root_path / rel_asset
        parent_dir = self.ensure_folder(asset_path.parent)
        anode = EntityNode(
            id=rel_asset,
            name=asset_path.name,
            node_type=NodeType.ASSET,
            file_path=rel_asset,
        )
        self.graph.add_node(rel_asset, **anode.to_dict())
        self._add_edge(parent_dir, rel_asset, EdgeType.CONTAINS)
        return rel_asset

    # incremental update for file modification or deletion
    def update_file(self, fpath: Path) -> None:
        with self.lock:
            rel_file = fpath.resolve().relative_to(self.root_path).as_posix()
            self.remove_path(rel_file)
            if fpath.exists() and fpath.is_file() and self._is_indexable(fpath):
                parent_dir = self.ensure_folder(fpath.parent)
                indexed = self._index_single_file(fpath, parent_dir)
                self.resolver.set_known_files(set(self._index.parsed_files.keys()))
                if indexed and indexed in self._index.parsed_files:
                    self._linker.link_file(self._index.parsed_files[indexed])

    def remove_path(self, rel_path: str) -> None:
        with self.lock:
            nodes_to_remove = self._index.unregister_file(rel_path)
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
