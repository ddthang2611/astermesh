import tree_sitter

from astermesh.parser.extractors.common import collect_body_usages, node_lines, node_text
from astermesh.parser.types import FileParseResult, RawEntity


def extract_python(
    root: tree_sitter.Node, source: bytes, scope_fqn: str, result: FileParseResult
) -> None:
    """Extract Python imports, classes, functions, methods, and module variables."""
    _walk_py_scope(root, source, scope_fqn, scope_fqn, result, is_class_body=False)


def _walk_py_scope(
    node: tree_sitter.Node,
    source: bytes,
    scope_fqn: str,
    parent_fqn: str,
    result: FileParseResult,
    is_class_body: bool,
) -> None:
    for child in node.children:
        ctype = child.type
        if ctype == "import_statement":
            _parse_py_import(child, source, result)
        elif ctype == "import_from_statement":
            _parse_py_from_import(child, source, result)
        elif ctype == "class_definition":
            _parse_py_class(child, source, scope_fqn, parent_fqn, result)
        elif ctype == "function_definition":
            _parse_py_function(child, source, scope_fqn, parent_fqn, result, is_class_body)
        elif ctype == "decorated_definition":
            _walk_py_scope(child, source, scope_fqn, parent_fqn, result, is_class_body)
        elif ctype == "expression_statement" and not is_class_body:
            _parse_py_variable(child, source, scope_fqn, result)


def _parse_py_import(
    node: tree_sitter.Node, source: bytes, result: FileParseResult
) -> None:
    for child in node.children:
        if child.type == "dotted_name":
            mod = node_text(child, source)
            result.imports[mod] = mod
        elif child.type == "aliased_import":
            name_node = child.child_by_field_name("name")
            alias_node = child.child_by_field_name("alias")
            if name_node and alias_node:
                result.imports[node_text(alias_node, source)] = node_text(name_node, source)


def _resolve_relative_module(raw_mod: str, current_module: str) -> str:
    if not raw_mod.startswith("."):
        return raw_mod
    dots = len(raw_mod) - len(raw_mod.lstrip("."))
    remainder = raw_mod.lstrip(".")
    parts = current_module.split(".")
    base_parts = parts[:-dots] if dots <= len(parts) else []
    if remainder:
        base_parts.append(remainder)
    return ".".join(base_parts)


def _parse_py_from_import(
    node: tree_sitter.Node, source: bytes, result: FileParseResult
) -> None:
    mod_node = node.child_by_field_name("module_name")
    raw_mod = node_text(mod_node, source) if mod_node else ""
    mod = _resolve_relative_module(raw_mod, result.module_fqn)
    for child in node.children:
        if child.type == "dotted_name" and child != mod_node:
            sym = node_text(child, source)
            result.imports[sym] = f"{mod}.{sym}" if mod else sym
        elif child.type == "aliased_import":
            name_node = child.child_by_field_name("name")
            alias_node = child.child_by_field_name("alias")
            if name_node and alias_node:
                sym = node_text(name_node, source)
                alias = node_text(alias_node, source)
                result.imports[alias] = f"{mod}.{sym}" if mod else sym


def _parse_py_class(
    node: tree_sitter.Node,
    source: bytes,
    scope_fqn: str,
    parent_fqn: str,
    result: FileParseResult,
) -> None:
    name_node = node.child_by_field_name("name")
    if not name_node:
        return
    name = node_text(name_node, source)
    fqn = f"{scope_fqn}.{name}"
    l_start, l_end = node_lines(node)

    bases: list[str] = []
    super_node = node.child_by_field_name("superclasses")
    if super_node:
        for ch in super_node.children:
            if ch.type in ("identifier", "attribute"):
                bases.append(node_text(ch, source))

    result.entities.append(
        RawEntity(
            name=name,
            fqn=fqn,
            kind="class",
            line_start=l_start,
            line_end=l_end,
            parent_fqn=parent_fqn,
            bases=bases,
        )
    )

    body_node = node.child_by_field_name("body")
    if body_node:
        _walk_py_scope(body_node, source, fqn, fqn, result, is_class_body=True)


def _parse_py_function(
    node: tree_sitter.Node,
    source: bytes,
    scope_fqn: str,
    parent_fqn: str,
    result: FileParseResult,
    is_class_body: bool,
) -> None:
    name_node = node.child_by_field_name("name")
    if not name_node:
        return
    name = node_text(name_node, source)
    fqn = f"{scope_fqn}.{name}"
    l_start, l_end = node_lines(node)
    is_async = any(ch.type == "async" for ch in node.children)

    params: list[str] = []
    param_node = node.child_by_field_name("parameters")
    if param_node:
        for ch in param_node.children:
            if ch.type == "identifier":
                params.append(node_text(ch, source))
            elif ch.type in ("typed_parameter", "default_parameter", "typed_default_parameter"):
                for sub in ch.children:
                    if sub.type == "identifier":
                        params.append(node_text(sub, source))
                        break

    body_node = node.child_by_field_name("body")
    calls: list[str] = []
    refs: list[str] = []
    if body_node:
        calls, refs, _, _ = collect_body_usages(body_node, source)

    result.entities.append(
        RawEntity(
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
    )


def _parse_py_variable(
    node: tree_sitter.Node, source: bytes, scope_fqn: str, result: FileParseResult
) -> None:
    for child in node.children:
        if child.type == "assignment":
            left = child.child_by_field_name("left")
            if left and left.type == "identifier":
                name = node_text(left, source)
                l_start, l_end = node_lines(child)
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
