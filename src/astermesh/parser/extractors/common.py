import sys

import tree_sitter


def node_text(node: tree_sitter.Node, source: bytes) -> str:
    """Decode AST node bytes to UTF-8, interning short single-line identifiers."""
    raw = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
    if len(raw) <= 64 and "\n" not in raw:
        return sys.intern(raw)
    return raw


def node_lines(node: tree_sitter.Node, line_offset: int = 0) -> tuple[int, int]:
    """Return 1-based inclusive (start, end) line numbers for an AST node."""
    return node.start_point[0] + 1 + line_offset, node.end_point[0] + 1 + line_offset


def strip_quotes(text: str) -> str:
    """Strip matching surrounding quotes from a string literal."""
    if len(text) >= 2 and text[0] in ("'", '"', "`") and text[-1] == text[0]:
        return text[1:-1]
    return text


def collect_body_usages(
    node: tree_sitter.Node, source: bytes
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Collect calls, identifier references, JSX renders, and context providers in a body."""
    calls: list[str] = []
    refs: list[str] = []
    renders: list[str] = []
    provides: list[str] = []
    stack = [node]

    while stack:
        curr = stack.pop()
        ctype = curr.type
        if ctype in ("call", "call_expression"):
            fn_node = curr.child_by_field_name("function")
            if fn_node:
                calls.append(node_text(fn_node, source))
        elif ctype == "new_expression":
            ctor = curr.child_by_field_name("constructor")
            if ctor:
                calls.append(node_text(ctor, source))
        elif ctype in ("jsx_opening_element", "jsx_self_closing_element"):
            tag_node = curr.child_by_field_name("name")
            if tag_node:
                tag_name = node_text(tag_node, source)
                if tag_name.endswith(".Provider"):
                    provides.append(tag_name.removesuffix(".Provider"))
                if tag_name and (tag_name[0].isupper() or "." in tag_name):
                    renders.append(tag_name)
                    refs.append(tag_name.split(".")[0])
        elif ctype in ("identifier", "type_identifier"):
            refs.append(node_text(curr, source))
        stack.extend(curr.children)
    return calls, refs, renders, provides
