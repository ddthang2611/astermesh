import re

import tree_sitter

from astermesh.graph.model import NodeType
from astermesh.parser.extractors.common import node_text

_HOOK_RE = re.compile(r"^use[A-Z]")


def collect_pattern_names(
    node: tree_sitter.Node, source: bytes, out: set[str]
) -> None:
    """Collect binding identifiers inside identifier, destructuring, default, or rest patterns."""
    stack = [node]
    while stack:
        n = stack.pop()
        t = n.type
        if t in ("identifier", "shorthand_property_identifier_pattern"):
            out.add(node_text(n, source))
        elif t == "pair_pattern":
            val = n.child_by_field_name("value")
            if val:
                stack.append(val)
        elif t in ("assignment_pattern", "object_assignment_pattern"):
            left = n.child_by_field_name("left")
            if left:
                stack.append(left)
        elif t in ("required_parameter", "optional_parameter"):
            pat = n.child_by_field_name("pattern")
            if pat:
                stack.append(pat)
        elif t in ("object_pattern", "array_pattern", "rest_pattern"):
            stack.extend(n.named_children)


def collect_scope_locals(
    fn_node: tree_sitter.Node, body: tree_sitter.Node | None, source: bytes
) -> set[str]:
    """Collect parameter and local declaration names inside a callable scope."""
    # nested scopes are folded in on purpose so a local never falls through to another file
    names: set[str] = set()
    params = fn_node.child_by_field_name("parameters")
    if params:
        for ch in params.named_children:
            collect_pattern_names(ch, source, names)
    single = fn_node.child_by_field_name("parameter")
    if single:
        collect_pattern_names(single, source, names)
    if body is None:
        return names

    stack = [body]
    while stack:
        curr = stack.pop()
        ctype = curr.type
        if ctype == "variable_declarator":
            name_n = curr.child_by_field_name("name")
            if name_n:
                collect_pattern_names(name_n, source, names)
        elif ctype in (
            "function_declaration",
            "generator_function_declaration",
            "class_declaration",
        ):
            name_n = curr.child_by_field_name("name")
            if name_n:
                names.add(node_text(name_n, source))
        elif ctype == "formal_parameters":
            for ch in curr.named_children:
                collect_pattern_names(ch, source, names)
        elif ctype in ("arrow_function", "catch_clause"):
            p = curr.child_by_field_name("parameter")
            if p:
                collect_pattern_names(p, source, names)
        elif ctype == "for_in_statement":
            left = curr.child_by_field_name("left")
            if left:
                collect_pattern_names(left, source, names)
        stack.extend(curr.children)
    return names


def extract_ts_params(fn_node: tree_sitter.Node, source: bytes) -> list[str]:
    """Extract parameter names from a TS/JS callable node."""
    params: list[str] = []
    param_node = fn_node.child_by_field_name("parameters")
    if not param_node:
        single = fn_node.child_by_field_name("parameter")
        if single:
            params.append(node_text(single, source))
        return params

    for ch in param_node.children:
        if ch.type == "identifier":
            params.append(node_text(ch, source))
        elif ch.type in ("required_parameter", "optional_parameter"):
            pat = ch.child_by_field_name("pattern")
            if pat:
                params.append(node_text(pat, source))
    return params


def unwrap_callable_initializer(val_node: tree_sitter.Node) -> tree_sitter.Node | None:
    """Return the underlying function/arrow node, unwrapping memo/forwardRef HOC calls."""
    if val_node.type in ("arrow_function", "function_expression", "function"):
        return val_node
    if val_node.type == "call_expression":
        args = val_node.child_by_field_name("arguments")
        if args:
            for ch in args.children:
                if ch.type in ("arrow_function", "function_expression", "function"):
                    return ch
    return None


def classify_ts_callable(
    name: str, renders: list[str], body_node: tree_sitter.Node
) -> str:
    """Classify a TS/JS callable as hook, React component, or plain function."""
    if _HOOK_RE.match(name):
        return NodeType.HOOK.value
    if renders:
        return NodeType.COMPONENT.value
    if name and name[0].isupper():
        stack = [body_node]
        while stack:
            n = stack.pop()
            if n.type in ("jsx_element", "jsx_self_closing_element", "jsx_fragment"):
                return NodeType.COMPONENT.value
            stack.extend(n.children)
    return NodeType.FUNCTION.value
