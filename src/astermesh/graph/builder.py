import os
import threading
from pathlib import Path

import networkx as nx

from astermesh.graph.model import EdgeType, EntityNode, NodeType
from astermesh.parser.engine import ASTEngine, FileParseResult
from astermesh.parser.resolver import ModuleResolver, is_asset_path
from astermesh.parser.satellite import SatelliteParser

IGNORE_DIRS = frozenset({
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".agents",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    "coverage",
    ".next",
    ".nuxt",
    "xml-graph",
})

IGNORED_FILE_SUFFIXES = (".min.js", ".min.css", ".bundle.js", ".map")


def is_ignored_dir(path: Path | str) -> bool:
    parts = Path(path).parts
    for p in parts:
        if p in IGNORE_DIRS or p.endswith(".egg-info"):
            return True
        if p.startswith(".") and p not in {".", ".."}:
            return True
    return False


def is_ignored_file(path: Path | str) -> bool:
    lower = str(path).lower()
    return lower.endswith(IGNORED_FILE_SUFFIXES)


# in-memory graph management and incremental updater
class GraphBuilder:
    def __init__(self, root_path: Path) -> None:
        self.root_path = root_path.resolve()
        self.graph = nx.MultiDiGraph()
        self.parser = ASTEngine()
        self.satellite = SatelliteParser()
        self.resolver = ModuleResolver(self.root_path)
        self.lock = threading.RLock()
        self._symbol_table: dict[str, str] = {}
        self._short_names: dict[str, set[str]] = {}
        self._suffix_table: dict[str, set[str]] = {}
        self._parsed_files: dict[str, FileParseResult] = {}
        self._file_nodes: dict[str, set[str]] = {}

    def _add_edge(self, u: str, v: str, etype: EdgeType) -> None:
        self.graph.add_edge(u, v, key=etype.value, type=etype.value)

    def _register_suffixes(self, fqn: str) -> None:
        parts = fqn.split(".")
        for i in range(len(parts)):
            suffix = ".".join(parts[i:])
            self._suffix_table.setdefault(suffix, set()).add(fqn)

    def _unregister_suffixes(self, fqn: str) -> None:
        parts = fqn.split(".")
        for i in range(len(parts)):
            suffix = ".".join(parts[i:])
            if suffix in self._suffix_table:
                self._suffix_table[suffix].discard(fqn)

    # construct full two-pass graph
    def build_full(self) -> nx.MultiDiGraph:
        with self.lock:
            self.graph.clear()
            self._symbol_table.clear()
            self._short_names.clear()
            self._suffix_table.clear()
            self._parsed_files.clear()
            self._file_nodes.clear()
            self.resolver.reload_config()

            self._scan_filesystem()
            self.resolver.set_known_files(set(self._parsed_files.keys()))

            for parsed in list(self._parsed_files.values()):
                self._link_file_relations(parsed)
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
                if is_ignored_file(fname):
                    continue
                if self.parser.supports(fpath.suffix.lower()) or self.satellite.supports(fpath):
                    self._index_single_file(fpath, rel_dir)

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
                is_exported=ent.is_exported,
            )
            self.graph.add_node(ent.fqn, **enode.to_dict())
            created_nodes.add(ent.fqn)

            self._symbol_table[ent.fqn] = ent.fqn
            if ntype not in (NodeType.CONFIG, NodeType.DOC):
                self._short_names.setdefault(ent.name, set()).add(ent.fqn)
            self._register_suffixes(ent.fqn)

            def_source = rel_file if ent.parent_fqn == parsed.module_fqn else ent.parent_fqn
            if def_source and self.graph.has_node(def_source):
                self._add_edge(def_source, ent.fqn, EdgeType.DEFINES)

        self._file_nodes[rel_file] = created_nodes
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

    def _resolve_target_export(self, target_file_rel: str, imp_spec) -> str | None:
        target_parsed = self._parsed_files.get(target_file_rel)
        if not target_parsed:
            return target_file_rel
        if imp_spec.imported_symbol:
            cand = f"{target_parsed.module_fqn}.{imp_spec.imported_symbol}"
            if cand in self._symbol_table:
                return cand
        if imp_spec.is_namespace:
            return target_parsed.module_fqn
        if imp_spec.is_default:
            stem = Path(target_file_rel).stem
            for ent in target_parsed.entities:
                if ent.name == stem or ent.is_exported:
                    return ent.fqn
            if target_parsed.entities:
                return target_parsed.entities[0].fqn
        return target_file_rel

    # pass 2: link fqn static relationships and two-step asset edges
    def _link_file_relations(self, parsed: FileParseResult) -> None:
        rel_file = parsed.file_path
        local_aliases: dict[str, str] = {}
        asset_aliases: dict[str, tuple[str, str | None]] = {}
        consumed_assets: set[str] = set()

        # python static imports
        for alias, target_mod in parsed.imports.items():
            resolved = self._resolve_symbol(target_mod, parsed)
            if resolved and self.graph.has_node(resolved):
                self._add_edge(rel_file, resolved, EdgeType.IMPORTS)
                local_aliases[alias] = resolved

        # js/ts/vue/angular module resolution and lazy asset creation
        for imp in parsed.raw_import_specs:
            resolved_path, query_flag = self.resolver.resolve(rel_file, imp.raw_specifier)
            if not resolved_path:
                continue

            if resolved_path not in self._parsed_files and is_asset_path(resolved_path):
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

        # collect di types per enclosing class for member call resolution
        class_injects: dict[str, list[str]] = {
            ent.fqn: ent.injects
            for ent in parsed.entities
            if ent.injects
        }

        for ent in parsed.entities:
            extra_classes = class_injects.get(ent.parent_fqn or "", [])

            for rendered in ent.renders:
                root_id = rendered.split(".")[0]
                if root_id in asset_aliases:
                    asset_id, _ = asset_aliases[root_id]
                    self._add_edge(ent.fqn, asset_id, EdgeType.RENDERS)
                    consumed_assets.add(root_id)
                else:
                    target = self._resolve_symbol(rendered, parsed, local_aliases)
                    if target and target != ent.fqn and self.graph.has_node(target):
                        self._add_edge(ent.fqn, target, EdgeType.RENDERS)

            for di_type in ent.injects:
                target = self._resolve_symbol(di_type, parsed, local_aliases)
                if target and target != ent.fqn and self.graph.has_node(target):
                    self._add_edge(ent.fqn, target, EdgeType.INJECTS)

            for prov in ent.provides:
                target = self._resolve_symbol(prov, parsed, local_aliases)
                if target and target != ent.fqn and self.graph.has_node(target):
                    self._add_edge(ent.fqn, target, EdgeType.PROVIDES)

            for base in ent.bases:
                target = self._resolve_symbol(base, parsed, local_aliases)
                if target and target != ent.fqn and self.graph.has_node(target):
                    self._add_edge(ent.fqn, target, EdgeType.INHERITS)

            for call in ent.calls:
                root_id = call.split(".")[0]
                if root_id in asset_aliases:
                    asset_id, qflag = asset_aliases[root_id]
                    etype = EdgeType.RENDERS if qflag == "?react" else EdgeType.REFERENCES
                    self._add_edge(ent.fqn, asset_id, etype)
                    consumed_assets.add(root_id)
                else:
                    target = self._resolve_symbol(
                        call, parsed, local_aliases, hint_classes=extra_classes
                    )
                    if target and target != ent.fqn and self.graph.has_node(target):
                        self._add_edge(ent.fqn, target, EdgeType.CALLS)

            for ref in ent.references:
                root_id = ref.split(".")[0]
                if root_id in asset_aliases:
                    asset_id, qflag = asset_aliases[root_id]
                    etype = (
                        EdgeType.RENDERS
                        if (qflag == "?react" or root_id in ent.renders)
                        else EdgeType.REFERENCES
                    )
                    self._add_edge(ent.fqn, asset_id, etype)
                    consumed_assets.add(root_id)
                    continue

                target = self._resolve_symbol(ref, parsed, local_aliases)
                if (
                    target
                    and target != ent.fqn
                    and self.graph.has_node(target)
                    and self.graph.nodes[target].get("type") == NodeType.VARIABLE.value
                ):
                    self._add_edge(ent.fqn, target, EdgeType.REFERENCES)

        # fallback file-level edge for unused asset imports
        for alias_name, (asset_id, _) in asset_aliases.items():
            if alias_name not in consumed_assets:
                self._add_edge(rel_file, asset_id, EdgeType.IMPORTS)

    def _resolve_symbol(
        self,
        raw_name: str,
        parsed: FileParseResult,
        local_aliases: dict[str, str] | None = None,
        hint_classes: list[str] | None = None,
    ) -> str | None:
        clean = raw_name.removeprefix("self.").removeprefix("cls.").removeprefix("this.")

        if local_aliases:
            if clean in local_aliases:
                return local_aliases[clean]
            if "." in clean:
                head, tail = clean.split(".", 1)
                if head in local_aliases:
                    base_target = local_aliases[head]
                    candidate = f"{base_target}.{tail}"
                    if candidate in self._symbol_table:
                        return candidate

        local_candidate = f"{parsed.module_fqn}.{clean}"
        if local_candidate in self._symbol_table:
            return self._symbol_table[local_candidate]

        if clean in parsed.imports:
            imp_target = parsed.imports[clean]
            if imp_target in self._symbol_table:
                return self._symbol_table[imp_target]

        if clean in self._symbol_table:
            return self._symbol_table[clean]

        # try resolving member call against constructor-injected service types
        if hint_classes and "." in clean:
            method_name = clean.split(".")[-1]
            for hcls in hint_classes:
                cls_target = self._resolve_symbol(hcls, parsed, local_aliases)
                if cls_target:
                    cand = f"{cls_target}.{method_name}"
                    if cand in self._symbol_table:
                        return cand

        # try o(1) dot-path suffix resolution
        suffix_matches = self._suffix_table.get(clean)
        if suffix_matches and len(suffix_matches) == 1:
            return next(iter(suffix_matches))

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
            if (
                fpath.exists()
                and fpath.is_file()
                and not is_ignored_file(fpath.name)
                and (
                    self.parser.supports(fpath.suffix.lower())
                    or self.satellite.supports(fpath)
                )
            ):
                parent_dir = self.ensure_folder(fpath.parent)
                indexed = self._index_single_file(fpath, parent_dir)
                self.resolver.set_known_files(set(self._parsed_files.keys()))
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
                    self._unregister_suffixes(ent.fqn)
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
