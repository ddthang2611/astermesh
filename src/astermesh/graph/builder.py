import os
import re
import threading
from collections.abc import Sequence
from pathlib import Path

import networkx as nx

from astermesh.graph.filters import (
    IGNORE_DIRS,
    IGNORED_FILE_SUFFIXES,
    TEST_DATA_DIRS,
    is_ignored_dir,
    is_ignored_file,
    is_saved_webpage_dir,
    is_test_path,
    load_gitignore_patterns,
    matches_exclude_patterns,
)
from astermesh.graph.linker import RelationLinker
from astermesh.graph.model import EdgeType, EntityNode, NodeType
from astermesh.graph.symbol_index import SymbolIndex
from astermesh.languages.registry import LanguageRegistry, default_registry
from astermesh.parser.engine import ASTEngine
from astermesh.parser.resolver import ModuleResolver
from astermesh.parser.satellite import SatelliteParser
from astermesh.parser.types import FileParseResult

__all__ = [
    "IGNORED_FILE_SUFFIXES",
    "IGNORE_DIRS",
    "TEST_DATA_DIRS",
    "GraphBuilder",
    "is_ignored_dir",
    "is_ignored_file",
    "is_test_path",
]


_COLLISION_TAG_RE = re.compile(r"[^A-Za-z0-9_.-]+")


# in-memory graph management and incremental updater
class GraphBuilder:
    def __init__(
        self,
        root_path: Path,
        exclude_patterns: Sequence[str] | None = None,
        respect_gitignore: bool = False,
        registry: LanguageRegistry | None = None,
    ) -> None:
        self.root_path = root_path.resolve()
        patterns: list[str] = list(exclude_patterns) if exclude_patterns else []
        if respect_gitignore:
            patterns.extend(load_gitignore_patterns(self.root_path))
        self.exclude_patterns: tuple[str, ...] = tuple(patterns)
        self.respect_gitignore = respect_gitignore
        self.registry: LanguageRegistry = registry or default_registry()
        self.graph = nx.MultiDiGraph()
        self.parser = ASTEngine(registry=self.registry)
        self.satellite = SatelliteParser()
        self.resolver = ModuleResolver(self.root_path)
        self.lock = threading.RLock()
        self._index = SymbolIndex()
        self._linker = RelationLinker(
            self.graph,
            self._index,
            self.resolver,
            self._ensure_asset_node,
            registry=self.registry,
        )

    def is_excluded(self, rel_posix: str, is_dir: bool = False) -> bool:
        if not self.exclude_patterns:
            return False
        return matches_exclude_patterns(rel_posix, self.exclude_patterns, is_dir=is_dir)

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
            self._linker.reset_strategies()
            self.resolver.reload_config()

            self._scan_filesystem()
            self.resolver.set_known_files(set(self._index.parsed_files.keys()))

            for parsed in list(self._index.parsed_files.values()):
                self._linker.link_file(parsed)
            self._prune_link_payloads()
            self._linker.invalidate_caches()
            return self.graph

    def _prune_link_payloads(self) -> None:
        for parsed in self._index.parsed_files.values():
            if parsed.language != "java":
                continue
            for ent in parsed.entities:
                if ent.call_sites:
                    ent.call_sites.clear()
                if ent.var_types:
                    ent.var_types.clear()
                if ent.local_names:
                    ent.local_names.clear()
                if ent.calls:
                    ent.calls.clear()
            parsed.link_pruned = True

    # pass 1: scan directories and register symbols
    def _scan_filesystem(self) -> None:
        root_node = EntityNode(
            id=".",
            name=self.root_path.name,
            node_type=NodeType.FOLDER,
            file_path=".",
        )
        self.graph.add_node(".", **root_node.to_dict())
        plugin_ignores = self.registry.all_ignore_dirs()

        for dirpath, dirnames, filenames in os.walk(self.root_path):
            curr_dir = Path(dirpath)
            rel_dir = curr_dir.relative_to(self.root_path).as_posix()
            sibling_lower = {f.lower() for f in filenames}
            kept_dirs: list[str] = []
            for d in dirnames:
                if (
                    is_ignored_dir(d)
                    or d in plugin_ignores
                    or is_saved_webpage_dir(d, sibling_lower)
                ):
                    continue
                rel_sub = d if rel_dir == "." else f"{rel_dir}/{d}"
                if self.is_excluded(rel_sub, is_dir=True):
                    continue
                kept_dirs.append(d)
            dirnames[:] = kept_dirs

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
        plugin = self.registry.plugin_for_extension(fpath.suffix.lower())
        if plugin is None and not self.satellite.supports(fpath):
            return False
        # reject external and internal duplicate symlinks
        try:
            resolved = fpath.resolve()
            rel = resolved.relative_to(self.root_path).as_posix()
            if fpath.is_symlink() and resolved != fpath.parent.resolve() / fpath.name:
                return False
        except (OSError, ValueError):
            return False
        if self.is_excluded(rel, is_dir=False):
            return False
        if plugin is not None:
            return plugin.should_index(fpath, self.root_path, rel)
        # json/yaml fixtures under test folders flood the graph with config nodes
        return not self.registry.is_test_path(rel)

    def _disambiguate_colliding_fqns(self, parsed: FileParseResult, rel_file: str) -> None:
        mod_tag = _COLLISION_TAG_RE.sub("_", rel_file.split("/", 1)[0]) or "dup"
        fqn_remap: dict[str, str] = {}
        for ent in parsed.entities:
            if ent.parent_fqn and ent.parent_fqn in fqn_remap:
                old_parent = ent.parent_fqn
                new_parent = fqn_remap[old_parent]
                ent.parent_fqn = new_parent
                if ent.fqn.startswith(f"{old_parent}."):
                    new_fqn = f"{new_parent}{ent.fqn[len(old_parent) :]}"
                else:
                    new_fqn = f"{ent.fqn}#{mod_tag}"
                if ent.fqn != new_fqn:
                    fqn_remap[ent.fqn] = new_fqn
                    ent.fqn = new_fqn
            elif (
                self.graph.has_node(ent.fqn)
                and self.graph.nodes[ent.fqn].get("file_path") != rel_file
            ):
                cand = f"{ent.fqn}#{mod_tag}"
                suffix_idx = 2
                while (
                    self.graph.has_node(cand)
                    and self.graph.nodes[cand].get("file_path") != rel_file
                ):
                    cand = f"{ent.fqn}#{mod_tag}_{suffix_idx}"
                    suffix_idx += 1
                fqn_remap[ent.fqn] = cand
                ent.fqn = cand

        if fqn_remap and parsed.type_aliases:
            for alias, target in list(parsed.type_aliases.items()):
                if target in fqn_remap:
                    parsed.type_aliases[alias] = fqn_remap[target]

    def _index_single_file(self, fpath: Path, parent_dir: str) -> str | None:
        rel_file = fpath.relative_to(self.root_path).as_posix()
        parsed = (
            self.parser.parse_file(fpath, self.root_path)
            if self.parser.supports(fpath.suffix.lower())
            else self.satellite.parse_file(fpath, self.root_path)
        )
        if not parsed:
            return None

        self._disambiguate_colliding_fqns(parsed, rel_file)

        file_family = parsed.language_family or (
            "satellite" if parsed.language == "satellite" else ""
        )
        file_ns = parsed.namespace_fqn or parsed.module_fqn
        file_node = EntityNode(
            id=rel_file,
            name=fpath.name,
            node_type=NodeType.FILE,
            file_path=rel_file,
            language_family=file_family,
            namespace_fqn=file_ns,
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
                language_family=ent.language_family or parsed.language_family or "satellite",
                namespace_fqn=ent.namespace_fqn or parsed.namespace_fqn or parsed.module_fqn,
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

    def _index_and_link(self, fpath: Path) -> None:
        parent_dir = self.ensure_folder(fpath.parent)
        indexed = self._index_single_file(fpath, parent_dir)
        self.resolver.set_known_files(set(self._index.parsed_files.keys()))
        if indexed and indexed in self._index.parsed_files:
            self._linker.link_file(self._index.parsed_files[indexed])

    def _collect_incoming_dependents(self, rel_file: str) -> set[str]:
        dependents: set[str] = set()
        target_nodes = set(self._index.file_nodes.get(rel_file, ())) | {rel_file}
        for nid in target_nodes:
            if not self.graph.has_node(nid):
                continue
            for src_id, _, data in self.graph.in_edges(nid, data=True):
                if data.get("type") == EdgeType.CONTAINS.value:
                    continue
                src_file = self.graph.nodes[src_id].get("file_path")
                if (
                    isinstance(src_file, str)
                    and src_file != rel_file
                    and src_file in self._index.parsed_files
                ):
                    dependents.add(src_file)
        return dependents

    def _relink_dependents(self, dependents: set[str], excluded_file: str) -> None:
        for dep in sorted(dependents):
            if dep == excluded_file or dep not in self._index.parsed_files:
                continue
            parsed_dep = self._index.parsed_files[dep]
            if parsed_dep.link_pruned:
                fresh = self.parser.parse_file(self.root_path / dep, self.root_path)
                if fresh is not None:
                    if len(fresh.entities) == len(parsed_dep.entities):
                        for f_ent, p_ent in zip(fresh.entities, parsed_dep.entities, strict=True):
                            f_ent.fqn = p_ent.fqn
                            f_ent.parent_fqn = p_ent.parent_fqn
                    self._linker.link_file(fresh)
                    continue
            self._linker.link_file(parsed_dep)

    # incremental update for file modification or deletion
    def update_file(self, fpath: Path) -> None:
        with self.lock:
            try:
                candidate = fpath if fpath.is_absolute() else (self.root_path / fpath)
                resolved = candidate.resolve()
                rel_file = resolved.relative_to(self.root_path).as_posix()
            except (OSError, ValueError):
                return
            if candidate.is_symlink() and resolved != candidate.parent.resolve() / candidate.name:
                return
            dependents = self._collect_incoming_dependents(rel_file)
            self.remove_path(rel_file)
            self._linker.invalidate_caches()
            if resolved.exists() and resolved.is_file() and self._is_indexable(resolved):
                self._index_and_link(resolved)
                self._relink_dependents(dependents, rel_file)
                return
            self.resolver.set_known_files(set(self._index.parsed_files.keys()))
            if not resolved.exists():
                plugin = self.registry.plugin_for_extension(resolved.suffix.lower())
                if plugin is not None:
                    for cand in plugin.on_file_removed(resolved, self.root_path):
                        cand_resolved = cand.resolve()
                        if (
                            cand_resolved.exists()
                            and cand_resolved.is_file()
                            and self._is_indexable(cand_resolved)
                        ):
                            self._index_and_link(cand_resolved)
                            break
            self._relink_dependents(dependents, rel_file)

    def remove_path(self, rel_path: str) -> None:
        with self.lock:
            self._linker.invalidate_caches()
            nodes_to_remove = self._index.unregister_file(rel_path)
            for nid in nodes_to_remove:
                if self.graph.has_node(nid):
                    self.graph.remove_node(nid)

            if self.graph.has_node(rel_path):
                self.graph.remove_node(rel_path)

    def ensure_folder(self, dir_path: Path) -> str:
        with self.lock:
            try:
                rel_dir = dir_path.resolve().relative_to(self.root_path).as_posix()
            except (OSError, ValueError):
                return "."
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
