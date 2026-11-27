from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from astermesh.graph.model import NodeType
from astermesh.languages.java.di_and_codegen import (
    apply_di_and_codegen,
    extract_guice_bindings,
    is_guice_module,
    simple_annotation_name,
)
from astermesh.parser.types import CallSite, FileParseResult, RawEntity

if TYPE_CHECKING:
    from tree_sitter import Node

_TYPE_DECL_NODES = frozenset({
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
    "record_declaration",
    "annotation_type_declaration",
})
_BODY_NODES = frozenset({
    "class_body",
    "interface_body",
    "enum_body",
    "enum_body_declarations",
    "annotation_type_body",
})
_METHOD_NODES = frozenset({
    "method_declaration",
    "constructor_declaration",
    "compact_constructor_declaration",
})
_SERVICE_ANNOTATIONS = frozenset({
    "Service",
    "Repository",
    "Component",
    "RestController",
    "Controller",
    "Stateless",
})
_MODULE_ANNOTATIONS = frozenset({"Configuration", "SpringBootApplication"})
_PROVIDE_ANNOTATIONS = frozenset({"Bean", "Provides"})
_ACN2_RESERVED_RE = re.compile(r"[,@:>^~&\s]+")
_GENERIC_RE = re.compile(r"<[^<>]*>")


# pending method before overload FQN assignment
@dataclass
class _PendingMethod:
    ent: RawEntity
    arity: int
    sig_tokens: list[str]
    line_start: int


def _node_text(node: Node, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def strip_type_base(raw_type: str) -> str:
    """Strip generics and array brackets."""
    text = raw_type.strip()
    while "<" in text and ">" in text:
        updated = _GENERIC_RE.sub("", text)
        if updated == text:
            break
        text = updated
    text = text.replace("...", "").replace("[]", "").strip()
    return text


def signature_type_token(raw_type: str) -> str:
    """Produce ACN2-safe simple parameter type token."""
    text = raw_type.strip()
    while "<" in text and ">" in text:
        updated = _GENERIC_RE.sub("", text)
        if updated == text:
            break
        text = updated
    is_arr = text.endswith("...") or "[]" in text
    base = text.replace("...", "").replace("[]", "").strip()
    simple = base.rsplit(".", 1)[-1]
    if is_arr:
        simple = f"{simple}Arr"
    return _ACN2_RESERVED_RE.sub("", simple) or "Object"


def _extract_modifiers(node: Node, source_bytes: bytes) -> tuple[list[str], set[str]]:
    annotations: list[str] = []
    modifiers: set[str] = set()
    for child in node.children:
        if child.type != "modifiers":
            continue
        for mchild in child.children:
            if mchild.type in ("marker_annotation", "annotation"):
                name_node = mchild.child_by_field_name("name")
                if name_node is None:
                    name_node = next(
                        (
                            c
                            for c in mchild.children
                            if c.type in ("identifier", "scoped_identifier")
                        ),
                        None,
                    )
                if name_node is not None:
                    annotations.append(_node_text(name_node, source_bytes))
            elif not mchild.is_named:
                kw = _node_text(mchild, source_bytes).strip()
                if kw:
                    modifiers.add(kw)
    return annotations, modifiers


def _extract_package(root: Node, source_bytes: bytes) -> str:
    for child in root.children:
        if child.type == "package_declaration":
            for sub in child.children:
                if sub.type in ("scoped_identifier", "identifier"):
                    return _node_text(sub, source_bytes)
    return ""


def _extract_imports(root: Node, source_bytes: bytes, result: FileParseResult) -> None:
    for child in root.children:
        if child.type != "import_declaration":
            continue
        is_static = any(
            not c.is_named and _node_text(c, source_bytes) == "static" for c in child.children
        )
        is_wildcard = any(c.type == "asterisk" for c in child.children)
        target_node = next(
            (c for c in child.children if c.type in ("scoped_identifier", "identifier")),
            None,
        )
        if target_node is None:
            continue
        target = _node_text(target_node, source_bytes)
        if is_static and is_wildcard:
            result.static_wildcards.append(target)
        elif is_static:
            member = target.rsplit(".", 1)[-1]
            result.static_imports[member] = target
        elif is_wildcard:
            result.wildcard_imports.append(target)
        else:
            simple = target.rsplit(".", 1)[-1]
            result.imports[simple] = target


def _extract_bases(type_node: Node, source_bytes: bytes) -> list[str]:
    bases: list[str] = []
    for child in type_node.children:
        if child.type == "superclass":
            for sub in child.children:
                if sub.is_named:
                    base = strip_type_base(_node_text(sub, source_bytes))
                    if base:
                        bases.append(base)
        elif child.type in ("super_interfaces", "extends_interfaces"):
            for sub in child.children:
                if sub.type == "type_list":
                    for tnode in sub.children:
                        if tnode.is_named:
                            base = strip_type_base(_node_text(tnode, source_bytes))
                            if base:
                                bases.append(base)
                elif sub.is_named:
                    base = strip_type_base(_node_text(sub, source_bytes))
                    if base:
                        bases.append(base)
    return bases


def _classify_type_kind(annotations: list[str], bases: list[str]) -> str:
    simple_anns = {simple_annotation_name(a) for a in annotations}
    if (simple_anns & _MODULE_ANNOTATIONS) or is_guice_module(bases):
        return NodeType.MODULE.value
    if simple_anns & _SERVICE_ANNOTATIONS:
        return NodeType.SERVICE.value
    return NodeType.CLASS.value


def _extract_params(
    params_node: Node | None, source_bytes: bytes
) -> tuple[list[str], list[str], list[str]]:
    names: list[str] = []
    base_types: list[str] = []
    sig_tokens: list[str] = []
    if params_node is None:
        return names, base_types, sig_tokens

    for child in params_node.children:
        if child.type == "formal_parameter":
            type_node = child.child_by_field_name("type")
            name_node = child.child_by_field_name("name")
            if type_node is None or name_node is None:
                continue
            raw_type = _node_text(type_node, source_bytes)
            dims = "".join(
                _node_text(c, source_bytes) for c in child.children if c.type == "dimensions"
            )
            full_raw = f"{raw_type}{dims}"
            names.append(_node_text(name_node, source_bytes))
            base_types.append(strip_type_base(full_raw))
            sig_tokens.append(signature_type_token(full_raw))
        elif child.type == "spread_parameter":
            type_node = next(
                (c for c in child.children if c.is_named and c.type != "variable_declarator"),
                None,
            )
            decl_node = next(
                (c for c in child.children if c.type == "variable_declarator"), None
            )
            if type_node is None or decl_node is None:
                continue
            name_node = decl_node.child_by_field_name("name")
            if name_node is None:
                continue
            raw_type = f"{_node_text(type_node, source_bytes)}..."
            names.append(_node_text(name_node, source_bytes))
            base_types.append(strip_type_base(raw_type))
            sig_tokens.append(signature_type_token(raw_type))
    return names, base_types, sig_tokens


def _infer_arg_type(
    arg_node: Node,
    source_bytes: bytes,
    var_types: dict[str, str],
    field_types: dict[str, str],
) -> str:
    ntype = arg_node.type
    if ntype == "identifier":
        name = _node_text(arg_node, source_bytes)
        return var_types.get(name) or field_types.get(name, "")
    if ntype == "string_literal":
        return "String"
    if ntype in ("decimal_integer_literal", "hex_integer_literal"):
        return "int"
    if ntype in ("decimal_floating_point_literal", "hex_floating_point_literal"):
        return "double"
    if ntype in ("true", "false"):
        return "boolean"
    if ntype == "character_literal":
        return "char"
    if ntype == "object_creation_expression":
        tnode = arg_node.child_by_field_name("type")
        if tnode is not None:
            return strip_type_base(_node_text(tnode, source_bytes))
    return ""


def _parse_arg_list(
    args_node: Node | None,
    source_bytes: bytes,
    var_types: dict[str, str],
    field_types: dict[str, str],
) -> tuple[int, tuple[str, ...], list[Node]]:
    if args_node is None:
        return 0, (), []
    arg_nodes = [c for c in args_node.children if c.is_named]
    if not arg_nodes:
        return 0, (), []
    arg_types = tuple(_infer_arg_type(a, source_bytes, var_types, field_types) for a in arg_nodes)
    return len(arg_nodes), arg_types, arg_nodes


def _encode_ret_hint(cs: CallSite) -> str:
    recv = cs.receiver_var or ""
    chain_part = ";".join(f"{n}:{a}" for n, a in cs.chain)
    return f"__ret__:{recv}|{cs.raw_name}|{cs.arity}|{chain_part}"


def _build_invocation_chain(
    inv_node: Node,
    source_bytes: bytes,
    var_types: dict[str, str],
    field_types: dict[str, str],
) -> tuple[CallSite, list[Node]]:
    steps: list[tuple[str, int, tuple[str, ...], Node | None]] = []
    extra_nodes: list[Node] = []
    curr: Node | None = inv_node
    root_obj: Node | None = None

    while curr is not None and curr.type == "method_invocation":
        name_node = curr.child_by_field_name("name")
        args_node = curr.child_by_field_name("arguments")
        obj_node = curr.child_by_field_name("object")
        m_name = _node_text(name_node, source_bytes) if name_node is not None else ""
        arity, arg_types, arg_nodes = _parse_arg_list(
            args_node, source_bytes, var_types, field_types
        )
        steps.append((m_name, arity, arg_types, obj_node))
        extra_nodes.extend(arg_nodes)
        if obj_node is not None and obj_node.type == "method_invocation":
            curr = obj_node
        else:
            root_obj = obj_node
            break

    steps.reverse()
    root_name, root_arity, root_arg_types, _ = steps[0]
    chain = tuple((n, a) for n, a, _, _ in steps[1:])

    receiver_var: str | None = None
    if root_obj is not None:
        if root_obj.type == "object_creation_expression":
            tnode = root_obj.child_by_field_name("type")
            if tnode is not None:
                receiver_var = strip_type_base(_node_text(tnode, source_bytes))
            extra_nodes.append(root_obj)
        else:
            receiver_var = _node_text(root_obj, source_bytes).strip()

    cs = CallSite(
        raw_name=root_name,
        arity=root_arity,
        receiver_var=receiver_var,
        chain=chain,
        arg_types=root_arg_types,
    )
    return cs, extra_nodes


def _collect_local_vars(
    body_node: Node,
    source_bytes: bytes,
    var_types: dict[str, str],
    field_types: dict[str, str],
) -> None:
    stack: list[Node] = [body_node]
    while stack:
        node = stack.pop()
        if node.type == "local_variable_declaration":
            type_node = node.child_by_field_name("type")
            if type_node is not None:
                raw_decl = _node_text(type_node, source_bytes).strip()
                for child in node.children:
                    if child.type != "variable_declarator":
                        continue
                    vname_node = child.child_by_field_name("name")
                    if vname_node is None:
                        continue
                    vname = _node_text(vname_node, source_bytes)
                    if raw_decl != "var":
                        var_types[vname] = strip_type_base(raw_decl)
                    else:
                        val_node = child.child_by_field_name("value")
                        if val_node is not None and val_node.type == "object_creation_expression":
                            tnode = val_node.child_by_field_name("type")
                            if tnode is not None:
                                var_types[vname] = strip_type_base(_node_text(tnode, source_bytes))
                        elif val_node is not None and val_node.type == "method_invocation":
                            cs, _ = _build_invocation_chain(
                                val_node, source_bytes, var_types, field_types
                            )
                            var_types[vname] = _encode_ret_hint(cs)
            continue
        if node.type in _TYPE_DECL_NODES:
            continue
        stack.extend(reversed(node.children))


def _collect_calls(
    body_node: Node,
    source_bytes: bytes,
    var_types: dict[str, str],
    field_types: dict[str, str],
    call_sites: list[CallSite],
) -> None:
    stack: list[Node] = [body_node]
    while stack:
        node = stack.pop()
        if node.type in _TYPE_DECL_NODES:
            continue
        if node.type == "method_invocation":
            cs, extra_nodes = _build_invocation_chain(node, source_bytes, var_types, field_types)
            call_sites.append(cs)
            stack.extend(reversed(extra_nodes))
            continue
        if node.type == "object_creation_expression":
            tnode = node.child_by_field_name("type")
            args_node = node.child_by_field_name("arguments")
            arity, arg_types, arg_nodes = _parse_arg_list(
                args_node, source_bytes, var_types, field_types
            )
            if tnode is not None:
                tname = strip_type_base(_node_text(tnode, source_bytes))
                call_sites.append(
                    CallSite(
                        raw_name="$init",
                        arity=arity,
                        receiver_var=tname,
                        arg_types=arg_types,
                    )
                )
            stack.extend(reversed(arg_nodes))
            for child in node.children:
                if child.type == "class_body":
                    stack.append(child)
            continue
        if node.type == "method_reference":
            named = [c for c in node.children if c.is_named]
            if len(named) >= 2:
                left_text = strip_type_base(_node_text(named[0], source_bytes))
                right_text = _node_text(named[-1], source_bytes)
                raw = "$init" if right_text == "new" else right_text
                call_sites.append(CallSite(raw_name=raw, arity=-1, receiver_var=left_text))
            elif len(named) == 1:
                left_text = strip_type_base(_node_text(named[0], source_bytes))
                if _node_text(node, source_bytes).endswith("::new"):
                    call_sites.append(CallSite(raw_name="$init", arity=-1, receiver_var=left_text))
            continue
        stack.extend(reversed(node.children))


def _assign_overload_fqns(
    owner_fqn: str,
    pending: list[_PendingMethod],
    result: FileParseResult,
    methods_by_owner: dict[str, list[RawEntity]],
) -> None:
    name_counts: dict[str, int] = {}
    arity_counts: dict[tuple[str, int], int] = {}
    for pm in pending:
        name_counts[pm.ent.name] = name_counts.get(pm.ent.name, 0) + 1
        key = (pm.ent.name, pm.arity)
        arity_counts[key] = arity_counts.get(key, 0) + 1

    used_fqns: set[str] = set()
    owner_methods: list[RawEntity] = []
    for pm in pending:
        m_name = pm.ent.name
        if name_counts[m_name] == 1:
            fqn = f"{owner_fqn}.{m_name}"
        elif arity_counts[(m_name, pm.arity)] == 1:
            fqn = f"{owner_fqn}.{m_name}#{pm.arity}"
        else:
            sig = "/".join(pm.sig_tokens)
            candidate = f"{owner_fqn}.{m_name}({sig})"
            fqn = f"{candidate}#L{pm.line_start}" if candidate in used_fqns else candidate
        used_fqns.add(fqn)
        pm.ent.fqn = fqn
        result.entities.append(pm.ent)
        owner_methods.append(pm.ent)
    methods_by_owner[owner_fqn] = owner_methods


def _extract_type_node(
    type_node: Node,
    source_bytes: bytes,
    parent_fqn: str,
    result: FileParseResult,
    class_entities: list[RawEntity],
    uninitialized_final_fields: dict[str, list[str]],
    all_nonstatic_fields: dict[str, list[str]],
    field_annotations: dict[str, dict[str, list[str]]],
    methods_by_owner: dict[str, list[RawEntity]],
) -> None:
    name_node = type_node.child_by_field_name("name")
    if name_node is None:
        return
    cls_name = _node_text(name_node, source_bytes)
    ns = result.namespace_fqn
    if parent_fqn == result.module_fqn:
        cls_fqn = f"{ns}.{cls_name}" if ns else f"{result.module_fqn}.{cls_name}"
    else:
        cls_fqn = f"{parent_fqn}.{cls_name}"

    anns, mods = _extract_modifiers(type_node, source_bytes)
    bases = _extract_bases(type_node, source_bytes)
    kind = _classify_type_kind(anns, bases)

    rec_param_names: list[str] = []
    rec_param_types: list[str] = []
    if type_node.type == "record_declaration":
        rec_params_node = type_node.child_by_field_name("parameters")
        rec_param_names, rec_param_types, _ = _extract_params(rec_params_node, source_bytes)

    cls_ent = RawEntity(
        name=cls_name,
        fqn=cls_fqn,
        kind=kind,
        line_start=type_node.start_point[0] + 1,
        line_end=type_node.end_point[0] + 1,
        parent_fqn=parent_fqn,
        parameters=rec_param_names,
        bases=bases,
        is_exported=("public" in mods or parent_fqn == result.module_fqn),
        language_family="java",
        namespace_fqn=ns,
        param_types=rec_param_types,
        annotations=anns,
        modifiers=mods,
    )
    for rname, rtype in zip(rec_param_names, rec_param_types, strict=True):
        cls_ent.field_types[rname] = rtype

    result.entities.append(cls_ent)
    class_entities.append(cls_ent)

    body_node = next((c for c in type_node.children if c.type in _BODY_NODES), None)
    if body_node is None:
        return

    body_members: list[Node] = []
    for child in body_node.children:
        if child.type == "enum_body_declarations":
            body_members.extend(child.children)
        else:
            body_members.append(child)

    pending_methods: list[_PendingMethod] = []
    cls_field_entities: list[RawEntity] = []
    cls_uninit_finals: list[str] = []
    cls_nonstatic: list[str] = []
    cls_field_anns: dict[str, list[str]] = {}

    for member in body_members:
        if member.type != "field_declaration":
            continue
        f_anns, f_mods = _extract_modifiers(member, source_bytes)
        ftype_node = member.child_by_field_name("type")
        if ftype_node is None:
            continue
        ftype = strip_type_base(_node_text(ftype_node, source_bytes))
        for sub in member.children:
            if sub.type != "variable_declarator":
                continue
            vname_node = sub.child_by_field_name("name")
            if vname_node is None:
                continue
            fname = _node_text(vname_node, source_bytes)
            has_init = sub.child_by_field_name("value") is not None
            cls_ent.field_types[fname] = ftype
            cls_field_anns[fname] = f_anns
            if "static" not in f_mods:
                cls_nonstatic.append(ftype)
                if "final" in f_mods and not has_init:
                    cls_uninit_finals.append(ftype)
            f_ent = RawEntity(
                name=fname,
                fqn=f"{cls_fqn}.{fname}",
                kind=NodeType.VARIABLE.value,
                line_start=member.start_point[0] + 1,
                line_end=member.end_point[0] + 1,
                parent_fqn=cls_fqn,
                is_exported=("public" in f_mods),
                language_family="java",
                namespace_fqn=ns,
                return_type=ftype,
                annotations=f_anns,
                modifiers=f_mods,
            )
            cls_field_entities.append(f_ent)
            result.entities.append(f_ent)

    for member in body_members:
        if member.type in _METHOD_NODES:
            m_anns, m_mods = _extract_modifiers(member, source_bytes)
            is_ctor = member.type in ("constructor_declaration", "compact_constructor_declaration")
            if is_ctor:
                m_name = "$init"
                ret_type: str | None = None
            else:
                mname_node = member.child_by_field_name("name")
                if mname_node is None:
                    continue
                m_name = _node_text(mname_node, source_bytes)
                ret_node = member.child_by_field_name("type")
                if ret_node is None or ret_node.type == "void_type":
                    ret_type = None
                else:
                    ret_type = strip_type_base(_node_text(ret_node, source_bytes))

            params_node = member.child_by_field_name("parameters")
            p_names, p_types, sig_tokens = _extract_params(params_node, source_bytes)
            var_types = {pn: pt for pn, pt in zip(p_names, p_types, strict=True) if pt}

            body = member.child_by_field_name("body")
            call_sites: list[CallSite] = []
            if body is not None:
                _collect_local_vars(body, source_bytes, var_types, cls_ent.field_types)
                _collect_calls(body, source_bytes, var_types, cls_ent.field_types, call_sites)

            provides: list[str] = []
            if ret_type and ({simple_annotation_name(a) for a in m_anns} & _PROVIDE_ANNOTATIONS):
                provides.append(ret_type)

            m_ent = RawEntity(
                name=m_name,
                fqn="",
                kind=NodeType.FUNCTION.value,
                line_start=member.start_point[0] + 1,
                line_end=member.end_point[0] + 1,
                parent_fqn=cls_fqn,
                parameters=p_names,
                provides=provides,
                is_method=True,
                is_exported=("public" in m_mods),
                language_family="java",
                namespace_fqn=ns,
                return_type=ret_type,
                param_types=p_types,
                var_types=var_types,
                call_sites=call_sites,
                annotations=m_anns,
                modifiers=m_mods,
            )
            if m_name == "configure" and is_guice_module(bases):
                extract_guice_bindings(member, source_bytes, m_ent)

            pending_methods.append(
                _PendingMethod(
                    ent=m_ent,
                    arity=len(p_names),
                    sig_tokens=sig_tokens,
                    line_start=m_ent.line_start,
                )
            )
        elif member.type in _TYPE_DECL_NODES:
            _extract_type_node(
                member,
                source_bytes,
                cls_fqn,
                result,
                class_entities,
                uninitialized_final_fields,
                all_nonstatic_fields,
                field_annotations,
                methods_by_owner,
            )

    method_names = {pm.ent.name for pm in pending_methods}
    for f_ent in cls_field_entities:
        if f_ent.name in method_names:
            f_ent.fqn = f"{cls_fqn}.{f_ent.name}#field"

    uninitialized_final_fields[cls_fqn] = cls_uninit_finals
    all_nonstatic_fields[cls_fqn] = cls_nonstatic
    field_annotations[cls_fqn] = cls_field_anns
    _assign_overload_fqns(cls_fqn, pending_methods, result, methods_by_owner)


def extract_java(root: Node, source_bytes: bytes, result: FileParseResult) -> None:
    """Extract Java package, imports, types, members."""
    result.language = "java"
    result.language_family = "java"
    result.namespace_fqn = _extract_package(root, source_bytes)
    _extract_imports(root, source_bytes, result)

    class_entities: list[RawEntity] = []
    uninitialized_final_fields: dict[str, list[str]] = {}
    all_nonstatic_fields: dict[str, list[str]] = {}
    field_annotations: dict[str, dict[str, list[str]]] = {}
    methods_by_owner: dict[str, list[RawEntity]] = {}

    for child in root.children:
        if child.type in _TYPE_DECL_NODES:
            _extract_type_node(
                child,
                source_bytes,
                result.module_fqn,
                result,
                class_entities,
                uninitialized_final_fields,
                all_nonstatic_fields,
                field_annotations,
                methods_by_owner,
            )

    apply_di_and_codegen(
        result,
        class_entities,
        uninitialized_final_fields,
        all_nonstatic_fields,
        field_annotations,
        methods_by_owner,
    )
