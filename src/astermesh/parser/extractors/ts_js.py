import re

import tree_sitter

from astermesh.graph.model import NodeType
from astermesh.parser.extractors.common import (
    collect_body_usages,
    node_lines,
    node_text,
    strip_quotes,
)
from astermesh.parser.extractors.ts_scope import (
    classify_ts_callable,
    collect_scope_locals,
    extract_ts_params,
    unwrap_callable_initializer,
)
from astermesh.parser.types import FileParseResult, ImportSpec, RawEntity

_TS_EXPORTABLE_DECLS = frozenset({
    "function_declaration",
    "generator_function_declaration",
    "class_declaration",
    "lexical_declaration",
    "variable_declaration",
    "interface_declaration",
    "type_alias_declaration",
})


def extract_ts_js(
    root: tree_sitter.Node,
    source: bytes,
    scope_fqn: str,
    result: FileParseResult,
    line_offset: int = 0,
) -> None:
    """Extract TS/JS/JSX/Angular imports, exports, callables, classes, and types."""
    _walk_ts_scope(root, source, scope_fqn, result, line_offset=line_offset)
    if result.exported_names:
        for ent in result.entities:
            if ent.parent_fqn == scope_fqn and ent.name in result.exported_names:
                ent.is_exported = True


def _walk_ts_scope(
    node: tree_sitter.Node,
    source: bytes,
    scope_fqn: str,
    result: FileParseResult,
    line_offset: int = 0,
) -> None:
    for child in node.children:
        ctype = child.type
        if ctype == "import_statement":
            _parse_ts_import(child, source, result)
        elif ctype == "export_statement":
            _parse_ts_export(child, source, scope_fqn, result, line_offset)
        elif ctype in ("function_declaration", "generator_function_declaration"):
            _parse_ts_function(
                child, source, scope_fqn, scope_fqn, result, line_offset=line_offset
            )
        elif ctype in ("lexical_declaration", "variable_declaration"):
            _parse_ts_variable_decl(
                child, source, scope_fqn, result, line_offset=line_offset
            )
        elif ctype == "class_declaration":
            _parse_ts_class(child, source, scope_fqn, result, line_offset=line_offset)
        elif ctype in ("interface_declaration", "type_alias_declaration"):
            _parse_ts_type_decl(
                child, source, scope_fqn, result, line_offset=line_offset
            )


def _parse_ts_import(
    node: tree_sitter.Node, source: bytes, result: FileParseResult
) -> None:
    src_node = node.child_by_field_name("source")
    if not src_node:
        return
    spec = strip_quotes(node_text(src_node, source))
    clause = next((c for c in node.children if c.type == "import_clause"), None)

    if not clause:
        result.raw_import_specs.append(
            ImportSpec(local_name=None, raw_specifier=spec, is_side_effect=True)
        )
        return

    for ch in clause.children:
        if ch.type == "identifier":
            result.raw_import_specs.append(
                ImportSpec(local_name=node_text(ch, source), raw_specifier=spec, is_default=True)
            )
        elif ch.type == "namespace_import":
            ident = next((c for c in ch.children if c.type == "identifier"), None)
            if ident:
                result.raw_import_specs.append(
                    ImportSpec(
                        local_name=node_text(ident, source),
                        raw_specifier=spec,
                        is_namespace=True,
                    )
                )
        elif ch.type == "named_imports":
            for sub in ch.children:
                if sub.type == "import_specifier":
                    name_n = sub.child_by_field_name("name")
                    alias_n = sub.child_by_field_name("alias")
                    if name_n:
                        orig = node_text(name_n, source)
                        local = node_text(alias_n, source) if alias_n else orig
                        result.raw_import_specs.append(
                            ImportSpec(
                                local_name=local,
                                raw_specifier=spec,
                                imported_symbol=orig,
                            )
                        )


def _parse_ts_export(
    node: tree_sitter.Node,
    source: bytes,
    scope_fqn: str,
    result: FileParseResult,
    line_offset: int,
) -> None:
    decorators = [c for c in node.children if c.type == "decorator"]

    # handle re-exports: export { a } from './b'
    src_node = node.child_by_field_name("source")
    if src_node:
        spec = strip_quotes(node_text(src_node, source))
        result.raw_import_specs.append(
            ImportSpec(local_name=None, raw_specifier=spec, is_side_effect=True)
        )

    decl = node.child_by_field_name("declaration")

    # export { a, b as c }  /  export default a
    if not src_node:
        for ch in node.children:
            if ch.type == "export_clause":
                for spec_n in ch.named_children:
                    if spec_n.type == "export_specifier":
                        local_n = spec_n.child_by_field_name("name")
                        if local_n:
                            result.exported_names.add(node_text(local_n, source))
        value_n = node.child_by_field_name("value")
        if value_n is not None and value_n.type == "identifier":
            result.exported_names.add(node_text(value_n, source))

    targets = [decl] if decl else [c for c in node.children if c.type in _TS_EXPORTABLE_DECLS]

    for target in targets:
        ttype = target.type
        if ttype in ("function_declaration", "generator_function_declaration"):
            _parse_ts_function(
                target,
                source,
                scope_fqn,
                scope_fqn,
                result,
                is_exported=True,
                line_offset=line_offset,
            )
        elif ttype in ("lexical_declaration", "variable_declaration"):
            _parse_ts_variable_decl(
                target,
                source,
                scope_fqn,
                result,
                is_exported=True,
                line_offset=line_offset,
            )
        elif ttype == "class_declaration":
            _parse_ts_class(
                target,
                source,
                scope_fqn,
                result,
                extra_decorators=decorators,
                is_exported=True,
                line_offset=line_offset,
            )
        elif ttype in ("interface_declaration", "type_alias_declaration"):
            _parse_ts_type_decl(
                target,
                source,
                scope_fqn,
                result,
                is_exported=True,
                line_offset=line_offset,
            )


def _parse_ts_function(
    node: tree_sitter.Node,
    source: bytes,
    scope_fqn: str,
    parent_fqn: str,
    result: FileParseResult,
    is_method: bool = False,
    is_exported: bool = False,
    line_offset: int = 0,
) -> None:
    name_node = node.child_by_field_name("name")
    if not name_node:
        return
    name = node_text(name_node, source)
    l_start, l_end = node_lines(node, line_offset)
    is_async = any(ch.type == "async" for ch in node.children)
    params = extract_ts_params(node, source)

    body = node.child_by_field_name("body")
    calls, refs, renders, provides = (
        collect_body_usages(body, source) if body else ([], [], [], [])
    )
    kind = (
        NodeType.FUNCTION.value
        if is_method
        else classify_ts_callable(name, renders, body or node)
    )

    result.entities.append(
        RawEntity(
            name=name,
            fqn=f"{scope_fqn}.{name}",
            kind=kind,
            line_start=l_start,
            line_end=l_end,
            parent_fqn=parent_fqn,
            parameters=params,
            calls=calls,
            references=refs,
            renders=renders,
            provides=provides,
            local_names=collect_scope_locals(node, body, source),
            is_async=is_async,
            is_method=is_method,
            is_exported=is_exported,
        )
    )


def _parse_ts_variable_decl(
    node: tree_sitter.Node,
    source: bytes,
    scope_fqn: str,
    result: FileParseResult,
    is_exported: bool = False,
    line_offset: int = 0,
) -> None:
    for child in node.children:
        if child.type != "variable_declarator":
            continue
        name_node = child.child_by_field_name("name")
        val_node = child.child_by_field_name("value")
        if not name_node or name_node.type != "identifier":
            continue

        name = node_text(name_node, source)
        l_start, l_end = node_lines(child, line_offset)

        if val_node:
            # check commonjs require('...')
            if val_node.type == "call_expression":
                fn_n = val_node.child_by_field_name("function")
                if fn_n and node_text(fn_n, source) == "require":
                    args_n = val_node.child_by_field_name("arguments")
                    if args_n:
                        str_n = next((c for c in args_n.children if c.type == "string"), None)
                        if str_n:
                            spec = strip_quotes(node_text(str_n, source))
                            result.raw_import_specs.append(
                                ImportSpec(local_name=name, raw_specifier=spec, is_default=True)
                            )
                            continue

            callable_node = unwrap_callable_initializer(val_node)
            if callable_node:
                is_async = any(ch.type == "async" for ch in callable_node.children)
                params = extract_ts_params(callable_node, source)
                body = callable_node.child_by_field_name("body") or callable_node
                calls, refs, renders, provides = collect_body_usages(body, source)
                kind = classify_ts_callable(name, renders, body)
                result.entities.append(
                    RawEntity(
                        name=name,
                        fqn=f"{scope_fqn}.{name}",
                        kind=kind,
                        line_start=l_start,
                        line_end=l_end,
                        parent_fqn=scope_fqn,
                        parameters=params,
                        calls=calls,
                        references=refs,
                        renders=renders,
                        provides=provides,
                        local_names=collect_scope_locals(callable_node, body, source),
                        is_async=is_async,
                        is_exported=is_exported,
                    )
                )
                continue

        calls, refs, renders, provides = (
            collect_body_usages(val_node, source) if val_node else ([], [], [], [])
        )
        result.entities.append(
            RawEntity(
                name=name,
                fqn=f"{scope_fqn}.{name}",
                kind=NodeType.VARIABLE.value,
                line_start=l_start,
                line_end=l_end,
                parent_fqn=scope_fqn,
                calls=calls,
                references=refs,
                renders=renders,
                provides=provides,
                local_names=collect_scope_locals(val_node, val_node, source) if val_node else set(),
                is_exported=is_exported,
            )
        )


def _parse_ts_class(
    node: tree_sitter.Node,
    source: bytes,
    scope_fqn: str,
    result: FileParseResult,
    extra_decorators: list[tree_sitter.Node] | None = None,
    is_exported: bool = False,
    line_offset: int = 0,
) -> None:
    name_node = node.child_by_field_name("name")
    if not name_node:
        return
    name = node_text(name_node, source)
    fqn = f"{scope_fqn}.{name}"
    l_start, l_end = node_lines(node, line_offset)

    decorators = list(extra_decorators or [])
    decorators.extend(c for c in node.children if c.type == "decorator")

    kind = NodeType.CLASS.value
    provides: list[str] = []
    bases: list[str] = []

    for dec in decorators:
        dec_text = node_text(dec, source)
        if "@Component" in dec_text:
            kind = NodeType.COMPONENT.value
            _extract_decorator_assets(dec_text, result)
        elif "@Injectable" in dec_text:
            kind = NodeType.SERVICE.value
        elif "@NgModule" in dec_text or "@Module" in dec_text:
            kind = NodeType.MODULE.value
            provides.extend(_extract_module_provides(dec))

    for ch in node.children:
        if ch.type == "class_heritage":
            for clause in ch.children:
                if clause.type in ("extends_clause", "implements_clause"):
                    for sub in clause.children:
                        if sub.type in ("identifier", "type_identifier", "generic_type"):
                            bases.append(node_text(sub, source).split("<")[0])

    class_entity = RawEntity(
        name=name,
        fqn=fqn,
        kind=kind,
        line_start=l_start,
        line_end=l_end,
        parent_fqn=scope_fqn,
        bases=bases,
        provides=provides,
        is_exported=is_exported,
    )
    result.entities.append(class_entity)

    body = node.child_by_field_name("body")
    if not body:
        return

    for member in body.children:
        if member.type == "method_definition":
            m_name_n = member.child_by_field_name("name")
            m_name = node_text(m_name_n, source) if m_name_n else ""
            if m_name == "constructor":
                class_entity.injects.extend(_extract_constructor_di(member, source))
            _parse_ts_function(
                member,
                source,
                fqn,
                fqn,
                result,
                is_method=True,
                line_offset=line_offset,
            )
        elif member.type in ("public_field_definition", "property_definition"):
            fname_n = member.child_by_field_name("name")
            fval_n = member.child_by_field_name("value")
            if fname_n and fval_n:
                callable_n = unwrap_callable_initializer(fval_n)
                if callable_n:
                    m_name = node_text(fname_n, source)
                    ml_start, ml_end = node_lines(member, line_offset)
                    m_body = callable_n.child_by_field_name("body") or callable_n
                    calls, refs, renders, m_prov = collect_body_usages(m_body, source)
                    result.entities.append(
                        RawEntity(
                            name=m_name,
                            fqn=f"{fqn}.{m_name}",
                            kind=NodeType.FUNCTION.value,
                            line_start=ml_start,
                            line_end=ml_end,
                            parent_fqn=fqn,
                            parameters=extract_ts_params(callable_n, source),
                            calls=calls,
                            references=refs,
                            renders=renders,
                            provides=m_prov,
                            local_names=collect_scope_locals(callable_n, m_body, source),
                            is_method=True,
                        )
                    )


def _extract_decorator_assets(dec_text: str, result: FileParseResult) -> None:
    # capture angular templateUrl and styleUrls paths
    for m in re.finditer(
        r"""(?:templateUrl|styleUrls?)\s*:\s*\[?\s*['"]([^'"]+)['"]""", dec_text
    ):
        result.raw_import_specs.append(
            ImportSpec(local_name=None, raw_specifier=m.group(1), is_side_effect=True)
        )


def _extract_module_provides(dec_node: tree_sitter.Node) -> list[str]:
    refs: list[str] = []
    stack = [dec_node]
    while stack:
        curr = stack.pop()
        if curr.type == "identifier":
            refs.append(curr.text.decode("utf-8", errors="replace") if curr.text else "")
        stack.extend(curr.children)
    return [r for r in refs if r and r[0].isupper()]


def _extract_constructor_di(ctor_node: tree_sitter.Node, source: bytes) -> list[str]:
    di_types: list[str] = []
    param_node = ctor_node.child_by_field_name("parameters")
    if not param_node:
        return di_types

    for ch in param_node.children:
        if ch.type in ("required_parameter", "optional_parameter"):
            type_ann = ch.child_by_field_name("type")
            if type_ann:
                for sub in type_ann.children:
                    if sub.type in ("type_identifier", "identifier"):
                        tname = node_text(sub, source)
                        if tname not in ("string", "number", "boolean", "any", "unknown"):
                            di_types.append(tname)
                    elif sub.type == "generic_type":
                        base_t = sub.child_by_field_name("name")
                        if base_t:
                            di_types.append(node_text(base_t, source))
    return di_types


def _parse_ts_type_decl(
    node: tree_sitter.Node,
    source: bytes,
    scope_fqn: str,
    result: FileParseResult,
    is_exported: bool = False,
    line_offset: int = 0,
) -> None:
    name_node = node.child_by_field_name("name")
    if not name_node:
        return
    name = node_text(name_node, source)
    l_start, l_end = node_lines(node, line_offset)
    bases: list[str] = []
    for ch in node.children:
        if ch.type == "extends_type_clause":
            for sub in ch.children:
                if sub.type in ("type_identifier", "identifier"):
                    bases.append(node_text(sub, source))

    result.entities.append(
        RawEntity(
            name=name,
            fqn=f"{scope_fqn}.{name}",
            kind=NodeType.CLASS.value,
            line_start=l_start,
            line_end=l_end,
            parent_fqn=scope_fqn,
            bases=bases,
            is_exported=is_exported,
        )
    )
