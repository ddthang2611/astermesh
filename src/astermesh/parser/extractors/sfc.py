import re
from pathlib import Path

import tree_sitter

from astermesh.graph.model import NodeType
from astermesh.parser.extractors.ts_js import extract_ts_js
from astermesh.parser.types import FileParseResult, RawEntity

_SCRIPT_BLOCK_RE = re.compile(
    r"<script([^>]*)>(.*?)</script>", re.IGNORECASE | re.DOTALL
)
_TEMPLATE_BLOCK_RE = re.compile(
    r"<template[^>]*>(.*?)</template>", re.IGNORECASE | re.DOTALL
)
_HTML_TAG_RE = re.compile(r"<([A-Za-z][A-Za-z0-9_.-]*)\b")

_STANDARD_HTML_TAGS = frozenset({
    "a", "abbr", "address", "area", "article", "aside", "audio", "b", "base",
    "bdi", "bdo", "blockquote", "body", "br", "button", "canvas", "caption",
    "cite", "code", "col", "colgroup", "data", "datalist", "dd", "del",
    "details", "dfn", "dialog", "div", "dl", "dt", "em", "embed", "fieldset",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5",
    "h6", "head", "header", "hr", "html", "i", "iframe", "img", "input",
    "ins", "kbd", "label", "legend", "li", "link", "main", "map", "mark",
    "meta", "meter", "nav", "noscript", "object", "ol", "optgroup", "option",
    "output", "p", "param", "picture", "pre", "progress", "q", "rp", "rt",
    "ruby", "s", "samp", "script", "section", "select", "slot", "small",
    "source", "span", "strong", "style", "sub", "summary", "sup", "svg",
    "path", "circle", "rect", "line", "polygon", "polyline", "g", "defs",
    "table", "tbody", "td", "template", "textarea", "tfoot", "th", "thead",
    "time", "title", "tr", "track", "transition", "transition-group", "u",
    "ul", "var", "video", "wbr", "keep-alive", "component", "router-view",
    "router-link", "nuxt-link",
})


def _kebab_to_pascal(tag: str) -> str:
    if "-" not in tag:
        return tag
    return "".join(part.capitalize() for part in tag.split("-") if part)


def extract_template_tags(markup: str) -> list[str]:
    """Extract custom component tags from Vue/Svelte markup, normalizing kebab-case."""
    tags: list[str] = []
    seen: set[str] = set()
    for m in _HTML_TAG_RE.finditer(markup):
        raw_tag = m.group(1)
        if raw_tag.lower() in _STANDARD_HTML_TAGS:
            continue
        pascal = _kebab_to_pascal(raw_tag)
        if (raw_tag[0].isupper() or "-" in raw_tag) and pascal not in seen:
            seen.add(pascal)
            tags.append(pascal)
    return tags


def parse_sfc(
    source_bytes: bytes,
    rel_path: str,
    module_fqn: str,
    ext: str,
    parsers: dict[str, tree_sitter.Parser],
) -> FileParseResult:
    """Parse Vue or Svelte single-file components into entities and template renders."""
    text = source_bytes.decode("utf-8", errors="replace")
    comp_name = Path(rel_path).stem
    result = FileParseResult(
        file_path=rel_path,
        module_fqn=module_fqn,
        language="vue" if ext == ".vue" else "svelte",
    )

    template_tags: list[str] = []
    if ext == ".vue":
        tpl_match = _TEMPLATE_BLOCK_RE.search(text)
        if tpl_match:
            template_tags = extract_template_tags(tpl_match.group(1))
    else:
        cleaned = _SCRIPT_BLOCK_RE.sub("", text)
        template_tags = extract_template_tags(cleaned)

    has_script_entities = False
    for m in _SCRIPT_BLOCK_RE.finditer(text):
        attrs = (m.group(1) or "").lower()
        script_code = m.group(2)
        line_offset = text[: m.start(2)].count("\n")

        if "tsx" in attrs:
            parser = parsers[".tsx"]
        elif "ts" in attrs:
            parser = parsers[".ts"]
        else:
            parser = parsers[".js"]

        script_bytes = script_code.encode("utf-8")
        tree = parser.parse(script_bytes)
        before_count = len(result.entities)
        extract_ts_js(
            tree.root_node, script_bytes, module_fqn, result, line_offset=line_offset
        )
        if len(result.entities) > before_count:
            has_script_entities = True

    # ensure primary component entity represents sfc file
    sfc_comp = next(
        (e for e in result.entities if e.kind == NodeType.COMPONENT.value),
        None,
    )
    if sfc_comp:
        sfc_comp.renders.extend(template_tags)
    elif template_tags or has_script_entities or text.strip():
        total_lines = max(1, text.count("\n") + 1)
        result.entities.append(
            RawEntity(
                name=comp_name,
                fqn=f"{module_fqn}.{comp_name}",
                kind=NodeType.COMPONENT.value,
                line_start=1,
                line_end=total_lines,
                parent_fqn=module_fqn,
                renders=template_tags,
                is_exported=True,
            )
        )
    return result
