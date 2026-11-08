from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import tree_sitter
import tree_sitter_javascript as tsjavascript
import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript


# thuc the tho boc tach tu ast
@dataclass
class RawEntity:
    name: str
    fqn: str
    kind: str
    line_start: int
    line_end: int
    parent_fqn: Optional[str] = None
    parameters: List[str] = field(default_factory=list)
    bases: List[str] = field(default_factory=list)
    calls: List[str] = field(default_factory=list)
    references: List[str] = field(default_factory=list)
    is_async: bool = False
    is_method: bool = False


# ket qua boc tach toan bo tep
@dataclass
class FileParseResult:
    file_path: str
    module_fqn: str
    entities: List[RawEntity] = field(default_factory=list)
    imports: Dict[str, str] = field(default_factory=dict)


# bo dieu phoi tree-sitter da ngon ngu
class ASTEngine:
    def __init__(self) -> None:
        self._languages: Dict[str, tree_sitter.Language] = {
            ".py": tree_sitter.Language(tspython.language()),
            ".js": tree_sitter.Language(tsjavascript.language()),
            ".ts": tree_sitter.Language(tstypescript.language_typescript()),
            ".tsx": tree_sitter.Language(tstypescript.language_tsx()),
        }

    def supports(self, ext: str) -> bool:
        return ext in self._languages

    def parse_file(self, file_path: Path, root_path: Path) -> Optional[FileParseResult]:
        ext = file_path.suffix.lower()
        if not self.supports(ext):
            return None

        try:
            source_bytes = file_path.read_bytes()
        except OSError:
            return None

        parser = tree_sitter.Parser(self._languages[ext])
        tree = parser.parse(source_bytes)
        rel_path = file_path.relative_to(root_path).as_posix()
        module_fqn = self._path_to_module(rel_path)

        result = FileParseResult(file_path=rel_path, module_fqn=module_fqn)
        if ext == ".py":
            self._extract_python(tree.root_node, source_bytes, module_fqn, result)
        else:
            self._extract_ts_js(tree.root_node, source_bytes, module_fqn, result)
        return result

    def _path_to_module(self, rel_path: str) -> str:
        stem = rel_path.rsplit(".", 1)[0]
        parts = [p for p in stem.split("/") if p != "__init__"]
        return ".".join(parts) if parts else "root"

    def _text(self, node: tree_sitter.Node, source: bytes) -> str:
        return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _lines(self, node: tree_sitter.Node) -> Tuple[int, int]:
        return node.start_point[0] + 1, node.end_point[0] + 1

    # boc tach ast python
    def _extract_python(
        self, root: tree_sitter.Node, source: bytes, scope_fqn: str, result: FileParseResult
    ) -> None:
        self._walk_py_scope(root, source, scope_fqn, result.module_fqn, result, is_class_body=False)

    def _walk_py_scope(
        self,
        node: tree_sitter.Node,
        source: bytes,
        scope_fqn: str,
        parent_fqn: str,
        result: FileParseResult,
        is_class_body: bool = False,
    ) -> None:
        for child in node.children:
            if child.type == "import_statement":
                self._parse_py_import(child, source, result)
            elif child.type == "import_from_statement":
                self._parse_py_from_import(child, source, result)
            elif child.type == "class_definition":
                self._parse_py_class(child, source, scope_fqn, parent_fqn, result)
            elif child.type in ("function_definition", "decorated_definition"):
                self._parse_py_function(child, source, scope_fqn, parent_fqn, result, is_class_body=is_class_body)
            elif child.type == "expression_statement" and scope_fqn == result.module_fqn:
                self._parse_py_variable(child, source, scope_fqn, result)

    def _parse_py_import(
        self, node: tree_sitter.Node, source: bytes, result: FileParseResult
    ) -> None:
        for child in node.children:
            if child.type == "dotted_name":
                name = self._text(child, source)
                result.imports[name.split(".")[-1]] = name
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node and alias_node:
                    result.imports[self._text(alias_node, source)] = self._text(name_node, source)

    def _resolve_relative_module(self, raw_mod: str, current_module: str) -> str:
        if not raw_mod.startswith("."):
            return raw_mod
        dot_count = len(raw_mod) - len(raw_mod.lstrip("."))
        sub_name = raw_mod.lstrip(".")
        parts = current_module.split(".")
        if len(parts) >= dot_count:
            base_parts = parts[:-dot_count]
        else:
            base_parts = []
        if sub_name:
            base_parts.append(sub_name)
        return ".".join(base_parts) if base_parts else "root"

    def _parse_py_from_import(
        self, node: tree_sitter.Node, source: bytes, result: FileParseResult
    ) -> None:
        mod_node = node.child_by_field_name("module_name")
        raw_mod = self._text(mod_node, source) if mod_node else ""
        mod_name = self._resolve_relative_module(raw_mod, result.module_fqn)

        for child in node.children:
            if child.type == "dotted_name" and child != mod_node:
                sym = self._text(child, source)
                result.imports[sym] = f"{mod_name}.{sym}" if mod_name else sym
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node and alias_node:
                    sym = self._text(name_node, source)
                    alias = self._text(alias_node, source)
                    result.imports[alias] = f"{mod_name}.{sym}" if mod_name else sym

    def _parse_py_class(
        self,
        node: tree_sitter.Node,
        source: bytes,
        scope_fqn: str,
        parent_fqn: str,
        result: FileParseResult,
    ) -> None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = self._text(name_node, source)
        fqn = f"{scope_fqn}.{name}"
        l_start, l_end = self._lines(node)
        bases: List[str] = []
        superclasses = node.child_by_field_name("superclasses")
        if superclasses:
            for arg in superclasses.children:
                if arg.type in ("identifier", "attribute"):
                    bases.append(self._text(arg, source))

        entity = RawEntity(
            name=name,
            fqn=fqn,
            kind="class",
            line_start=l_start,
            line_end=l_end,
            parent_fqn=parent_fqn,
            bases=bases,
        )
        result.entities.append(entity)
        body = node.child_by_field_name("body")
        if body:
            self._walk_py_scope(body, source, fqn, fqn, result, is_class_body=True)

    def _parse_py_function(
        self,
        node: tree_sitter.Node,
        source: bytes,
        scope_fqn: str,
        parent_fqn: str,
        result: FileParseResult,
        is_class_body: bool = False,
    ) -> None:
        actual = node
        if node.type == "decorated_definition":
            definition = node.child_by_field_name("definition")
            if not definition:
                return
            if definition.type == "class_definition":
                self._parse_py_class(definition, source, scope_fqn, parent_fqn, result)
                return
            actual = definition

        name_node = actual.child_by_field_name("name")
        if not name_node:
            return
        name = self._text(name_node, source)
        fqn = f"{scope_fqn}.{name}"
        l_start, l_end = self._lines(node)
        is_async = any(c.type == "async" for c in actual.children)

        params: List[str] = []
        params_node = actual.child_by_field_name("parameters")
        if params_node:
            for p in params_node.children:
                if p.type == "identifier":
                    params.append(self._text(p, source))
                elif p.type in ("typed_parameter", "default_parameter", "typed_default_parameter"):
                    p_name = p.child_by_field_name("name") or (p.children[0] if p.children else None)
                    if p_name:
                        params.append(self._text(p_name, source))

        calls, refs = self._collect_body_usages(actual, source)
        entity = RawEntity(
            name=name,
            fqn=fqn,
            kind="function",
            line_start=l_start,
            line_end=l_end,
            parent_fqn=parent_fqn,
            parameters=params,
            calls=calls,
            references=refs,
            is_async=is_async,
            is_method=is_class_body,
        )
        result.entities.append(entity)

    def _parse_py_variable(
        self, node: tree_sitter.Node, source: bytes, scope_fqn: str, result: FileParseResult
    ) -> None:
        for child in node.children:
            if child.type == "assignment":
                left = child.child_by_field_name("left")
                if left and left.type == "identifier":
                    name = self._text(left, source)
                    l_start, l_end = self._lines(child)
                    result.entities.append(
                        RawEntity(
                            name=name,
                            fqn=f"{scope_fqn}.{name}",
                            kind="variable",
                            line_start=l_start,
                            line_end=l_end,
                            parent_fqn=scope_fqn,
                        )
                    )

    def _collect_body_usages(
        self, node: tree_sitter.Node, source: bytes
    ) -> Tuple[List[str], List[str]]:
        calls: List[str] = []
        refs: List[str] = []
        stack = [node]
        while stack:
            curr = stack.pop()
            if curr.type == "call":
                fn_node = curr.child_by_field_name("function")
                if fn_node:
                    calls.append(self._text(fn_node, source))
            elif curr.type == "identifier":
                refs.append(self._text(curr, source))
            stack.extend(curr.children)
        return calls, refs

    # boc tach ast ts/js
    def _extract_ts_js(
        self, root: tree_sitter.Node, source: bytes, scope_fqn: str, result: FileParseResult
    ) -> None:
        for child in root.children:
            if child.type in ("function_declaration", "method_definition"):
                name_node = child.child_by_field_name("name")
                if name_node:
                    name = self._text(name_node, source)
                    l_start, l_end = self._lines(child)
                    calls, refs = self._collect_body_usages(child, source)
                    result.entities.append(
                        RawEntity(
                            name=name,
                            fqn=f"{scope_fqn}.{name}",
                            kind="function",
                            line_start=l_start,
                            line_end=l_end,
                            parent_fqn=scope_fqn,
                            calls=calls,
                            references=refs,
                        )
                    )
            elif child.type == "class_declaration":
                name_node = child.child_by_field_name("name")
                if name_node:
                    name = self._text(name_node, source)
                    fqn = f"{scope_fqn}.{name}"
                    l_start, l_end = self._lines(child)
                    result.entities.append(
                        RawEntity(
                            name=name,
                            fqn=fqn,
                            kind="class",
                            line_start=l_start,
                            line_end=l_end,
                            parent_fqn=scope_fqn,
                        )
                    )
                    body = child.child_by_field_name("body")
                    if body:
                        for member in body.children:
                            if member.type == "method_definition":
                                m_name_node = member.child_by_field_name("name")
                                if m_name_node:
                                    m_name = self._text(m_name_node, source)
                                    m_start, m_end = self._lines(member)
                                    calls, refs = self._collect_body_usages(member, source)
                                    result.entities.append(
                                        RawEntity(
                                            name=m_name,
                                            fqn=f"{fqn}.{m_name}",
                                            kind="function",
                                            line_start=m_start,
                                            line_end=m_end,
                                            parent_fqn=fqn,
                                            calls=calls,
                                            references=refs,
                                            is_method=True,
                                        )
                                    )
