import re
import sys
from pathlib import Path

import tree_sitter
import tree_sitter_javascript as tsjavascript
import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript

from astermesh.graph.model import NodeType
from astermesh.parser.types import FileParseResult, ImportSpec, RawEntity

__all__ = ["ASTEngine", "FileParseResult", "ImportSpec", "RawEntity"]

_HOOK_RE = re.compile(r"^use[A-Z]")
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


def _strip_quotes(text: str) -> str:
    if len(text) >= 2 and text[0] in ("'", '"', "`") and text[-1] == text[0]:
        return text[1:-1]
    return text


# multi-language tree-sitter orchestrator
class ASTEngine:
    def __init__(self) -> None:
        js_lang = tree_sitter.Language(tsjavascript.language())
        ts_lang = tree_sitter.Language(tstypescript.language_typescript())
        tsx_lang = tree_sitter.Language(tstypescript.language_tsx())
        py_lang = tree_sitter.Language(tspython.language())

        self._languages: dict[str, tree_sitter.Language] = {
            ".py": py_lang,
            ".js": js_lang,
            ".jsx": js_lang,
            ".mjs": js_lang,
            ".cjs": js_lang,
            ".ts": ts_lang,
            ".mts": ts_lang,
            ".cts": ts_lang,
            ".tsx": tsx_lang,
        }
        self._sfc_extensions = frozenset({".vue", ".svelte"})
        self._parsers: dict[str, tree_sitter.Parser] = {
            ext: tree_sitter.Parser(lang) for ext, lang in self._languages.items()
        }

    @property
    def supported_extensions(self) -> frozenset[str]:
        return frozenset(self._languages.keys()) | self._sfc_extensions

    def supports(self, ext: str) -> bool:
        lower = ext.lower()
        return lower in self._languages or lower in self._sfc_extensions

    def parse_file(self, file_path: Path, root_path: Path) -> FileParseResult | None:
        ext = file_path.suffix.lower()
        if not self.supports(ext):
            return None

        try:
            source_bytes = file_path.read_bytes()
        except OSError:
            return None

        rel_path = file_path.resolve().relative_to(root_path.resolve()).as_posix()
        module_fqn = self._path_to_module(rel_path)

        if ext in self._sfc_extensions:
            return self._parse_sfc(source_bytes, rel_path, module_fqn, ext)

        parser = self._parsers[ext]
        tree = parser.parse(source_bytes)
        lang_name = "python" if ext == ".py" else ("typescript" if "ts" in ext else "javascript")
        result = FileParseResult(file_path=rel_path, module_fqn=module_fqn, language=lang_name)

        if ext == ".py":
            self._extract_python(tree.root_node, source_bytes, module_fqn, result)
        else:
            self._extract_ts_js(tree.root_node, source_bytes, module_fqn, result)
        return result

    def _parse_sfc(
        self, source_bytes: bytes, rel_path: str, module_fqn: str, ext: str
    ) -> FileParseResult:
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
                template_tags = self._extract_template_tags(tpl_match.group(1))
        else:
            cleaned = _SCRIPT_BLOCK_RE.sub("", text)
            template_tags = self._extract_template_tags(cleaned)

        has_script_entities = False
        for match in _SCRIPT_BLOCK_RE.finditer(text):
            attrs = match.group(1).lower()
            script_code = match.group(2)
            line_offset = text[: match.start(2)].count("\n")

            if "tsx" in attrs:
                parser = self._parsers[".tsx"]
            elif "ts" in attrs:
                parser = self._parsers[".ts"]
            else:
                parser = self._parsers[".js"]

            script_bytes = script_code.encode("utf-8")
            tree = parser.parse(script_bytes)
            before_count = len(result.entities)
            self._extract_ts_js(
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

    def _extract_template_tags(self, markup: str) -> list[str]:
        tags: list[str] = []
        seen: set[str] = set()
        for m in _HTML_TAG_RE.finditer(markup):
            raw_tag = m.group(1)
            lower = raw_tag.lower()
            if lower in _STANDARD_HTML_TAGS:
                continue
            pascal = _kebab_to_pascal(raw_tag)
            if (raw_tag[0].isupper() or "-" in raw_tag) and pascal not in seen:
                seen.add(pascal)
                tags.append(pascal)
        return tags

    def _path_to_module(self, rel_path: str) -> str:
        p = Path(rel_path)
        parts = list(p.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts) if parts else "root"

    def _text(self, node: tree_sitter.Node, source: bytes) -> str:
        raw = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        if len(raw) <= 64 and "\n" not in raw:
            return sys.intern(raw)
        return raw

    def _lines(self, node: tree_sitter.Node, line_offset: int = 0) -> tuple[int, int]:
        return node.start_point[0] + 1 + line_offset, node.end_point[0] + 1 + line_offset

    # python ast extraction
    def _extract_python(
        self, root: tree_sitter.Node, source: bytes, scope_fqn: str, result: FileParseResult
    ) -> None:
        self._walk_py_scope(root, source, scope_fqn, scope_fqn, result, is_class_body=False)

    def _walk_py_scope(
        self,
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
                self._parse_py_import(child, source, result)
            elif ctype == "import_from_statement":
                self._parse_py_from_import(child, source, result)
            elif ctype == "class_definition":
                self._parse_py_class(child, source, scope_fqn, parent_fqn, result)
            elif ctype == "function_definition":
                self._parse_py_function(
                    child, source, scope_fqn, parent_fqn, result, is_class_body
                )
            elif ctype == "decorated_definition":
                self._walk_py_scope(
                    child, source, scope_fqn, parent_fqn, result, is_class_body
                )
            elif ctype == "expression_statement" and not is_class_body:
                self._parse_py_variable(child, source, scope_fqn, result)

    def _parse_py_import(
        self, node: tree_sitter.Node, source: bytes, result: FileParseResult
    ) -> None:
        for child in node.children:
            if child.type == "dotted_name":
                mod = self._text(child, source)
                result.imports[mod] = mod
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node and alias_node:
                    result.imports[self._text(alias_node, source)] = self._text(
                        name_node, source
                    )

    def _resolve_relative_module(self, raw_mod: str, current_module: str) -> str:
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
        self, node: tree_sitter.Node, source: bytes, result: FileParseResult
    ) -> None:
        mod_node = node.child_by_field_name("module_name")
        raw_mod = self._text(mod_node, source) if mod_node else ""
        mod = self._resolve_relative_module(raw_mod, result.module_fqn)
        for child in node.children:
            if child.type == "dotted_name" and child != mod_node:
                sym = self._text(child, source)
                result.imports[sym] = f"{mod}.{sym}" if mod else sym
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node and alias_node:
                    sym = self._text(name_node, source)
                    alias = self._text(alias_node, source)
                    result.imports[alias] = f"{mod}.{sym}" if mod else sym

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

        bases: list[str] = []
        super_node = node.child_by_field_name("superclasses")
        if super_node:
            for ch in super_node.children:
                if ch.type in ("identifier", "attribute"):
                    bases.append(self._text(ch, source))

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
            self._walk_py_scope(body_node, source, fqn, fqn, result, is_class_body=True)

    def _parse_py_function(
        self,
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
        name = self._text(name_node, source)
        fqn = f"{scope_fqn}.{name}"
        l_start, l_end = self._lines(node)
        is_async = any(ch.type == "async" for ch in node.children)

        params: list[str] = []
        param_node = node.child_by_field_name("parameters")
        if param_node:
            for ch in param_node.children:
                if ch.type == "identifier":
                    params.append(self._text(ch, source))
                elif ch.type in ("typed_parameter", "default_parameter", "typed_default_parameter"):
                    for sub in ch.children:
                        if sub.type == "identifier":
                            params.append(self._text(sub, source))
                            break

        body_node = node.child_by_field_name("body")
        calls: list[str] = []
        refs: list[str] = []
        if body_node:
            calls, refs, _, _ = self._collect_body_usages(body_node, source)

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

    # shared call, reference, and jsx usage collector
    def _collect_body_usages(
        self, node: tree_sitter.Node, source: bytes
    ) -> tuple[list[str], list[str], list[str], list[str]]:
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
                    calls.append(self._text(fn_node, source))
            elif ctype == "new_expression":
                ctor = curr.child_by_field_name("constructor")
                if ctor:
                    calls.append(self._text(ctor, source))
            elif ctype in ("jsx_opening_element", "jsx_self_closing_element"):
                tag_node = curr.child_by_field_name("name")
                if tag_node:
                    tag_name = self._text(tag_node, source)
                    if tag_name.endswith(".Provider"):
                        provides.append(tag_name.removesuffix(".Provider"))
                    if tag_name and (tag_name[0].isupper() or "." in tag_name):
                        renders.append(tag_name)
                        refs.append(tag_name.split(".")[0])
            elif ctype in ("identifier", "type_identifier"):
                refs.append(self._text(curr, source))
            stack.extend(curr.children)
        return calls, refs, renders, provides

    # typescript and javascript ast extraction
    def _extract_ts_js(
        self,
        root: tree_sitter.Node,
        source: bytes,
        scope_fqn: str,
        result: FileParseResult,
        line_offset: int = 0,
    ) -> None:
        self._walk_ts_scope(root, source, scope_fqn, result, line_offset=line_offset)

    def _walk_ts_scope(
        self,
        node: tree_sitter.Node,
        source: bytes,
        scope_fqn: str,
        result: FileParseResult,
        line_offset: int = 0,
    ) -> None:
        for child in node.children:
            ctype = child.type
            if ctype == "import_statement":
                self._parse_ts_import(child, source, result)
            elif ctype == "export_statement":
                self._parse_ts_export(child, source, scope_fqn, result, line_offset)
            elif ctype in ("function_declaration", "generator_function_declaration"):
                self._parse_ts_function(
                    child, source, scope_fqn, scope_fqn, result, line_offset=line_offset
                )
            elif ctype in ("lexical_declaration", "variable_declaration"):
                self._parse_ts_variable_decl(
                    child, source, scope_fqn, result, line_offset=line_offset
                )
            elif ctype == "class_declaration":
                self._parse_ts_class(
                    child, source, scope_fqn, result, line_offset=line_offset
                )
            elif ctype in ("interface_declaration", "type_alias_declaration"):
                self._parse_ts_type_decl(
                    child, source, scope_fqn, result, line_offset=line_offset
                )

    def _parse_ts_import(
        self, node: tree_sitter.Node, source: bytes, result: FileParseResult
    ) -> None:
        src_node = node.child_by_field_name("source")
        if not src_node:
            return
        spec = _strip_quotes(self._text(src_node, source))
        clause = next((c for c in node.children if c.type == "import_clause"), None)

        if not clause:
            result.raw_import_specs.append(
                ImportSpec(local_name=None, raw_specifier=spec, is_side_effect=True)
            )
            return

        for ch in clause.children:
            if ch.type == "identifier":
                local_name = self._text(ch, source)
                result.raw_import_specs.append(
                    ImportSpec(local_name=local_name, raw_specifier=spec, is_default=True)
                )
            elif ch.type == "namespace_import":
                ident = next((c for c in ch.children if c.type == "identifier"), None)
                if ident:
                    result.raw_import_specs.append(
                        ImportSpec(
                            local_name=self._text(ident, source),
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
                            orig = self._text(name_n, source)
                            local = self._text(alias_n, source) if alias_n else orig
                            result.raw_import_specs.append(
                                ImportSpec(
                                    local_name=local,
                                    raw_specifier=spec,
                                    imported_symbol=orig,
                                )
                            )

    def _parse_ts_export(
        self,
        node: tree_sitter.Node,
        source: bytes,
        scope_fqn: str,
        result: FileParseResult,
        line_offset: int,
    ) -> None:
        # collect decorators attached to export statement
        decorators = [c for c in node.children if c.type == "decorator"]

        # handle re-exports: export { a } from './b'
        src_node = node.child_by_field_name("source")
        if src_node:
            spec = _strip_quotes(self._text(src_node, source))
            result.raw_import_specs.append(
                ImportSpec(local_name=None, raw_specifier=spec, is_side_effect=True)
            )

        decl = node.child_by_field_name("declaration")
        targets = [decl] if decl else [
            c
            for c in node.children
            if c.type
            in (
                "function_declaration",
                "generator_function_declaration",
                "class_declaration",
                "lexical_declaration",
                "variable_declaration",
                "interface_declaration",
                "type_alias_declaration",
            )
        ]

        for target in targets:
            ttype = target.type
            if ttype in ("function_declaration", "generator_function_declaration"):
                self._parse_ts_function(
                    target,
                    source,
                    scope_fqn,
                    scope_fqn,
                    result,
                    is_exported=True,
                    line_offset=line_offset,
                )
            elif ttype in ("lexical_declaration", "variable_declaration"):
                self._parse_ts_variable_decl(
                    target,
                    source,
                    scope_fqn,
                    result,
                    is_exported=True,
                    line_offset=line_offset,
                )
            elif ttype == "class_declaration":
                self._parse_ts_class(
                    target,
                    source,
                    scope_fqn,
                    result,
                    extra_decorators=decorators,
                    is_exported=True,
                    line_offset=line_offset,
                )
            elif ttype in ("interface_declaration", "type_alias_declaration"):
                self._parse_ts_type_decl(
                    target,
                    source,
                    scope_fqn,
                    result,
                    is_exported=True,
                    line_offset=line_offset,
                )

    def _classify_ts_callable(self, name: str, renders: list[str], body_node: tree_sitter.Node) -> str:
        if _HOOK_RE.match(name):
            return NodeType.HOOK.value
        if renders:
            return NodeType.COMPONENT.value
        if name and name[0].isupper():
            # verify jsx return presence when upper-cased
            stack = [body_node]
            while stack:
                n = stack.pop()
                if n.type in ("jsx_element", "jsx_self_closing_element", "jsx_fragment"):
                    return NodeType.COMPONENT.value
                stack.extend(n.children)
        return NodeType.FUNCTION.value

    def _extract_ts_params(self, fn_node: tree_sitter.Node, source: bytes) -> list[str]:
        params: list[str] = []
        param_node = fn_node.child_by_field_name("parameters")
        if not param_node:
            single = fn_node.child_by_field_name("parameter")
            if single:
                params.append(self._text(single, source))
            return params

        for ch in param_node.children:
            if ch.type == "identifier":
                params.append(self._text(ch, source))
            elif ch.type in ("required_parameter", "optional_parameter"):
                pat = ch.child_by_field_name("pattern")
                if pat:
                    params.append(self._text(pat, source))
        return params

    def _parse_ts_function(
        self,
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
        name = self._text(name_node, source)
        fqn = f"{scope_fqn}.{name}"
        l_start, l_end = self._lines(node, line_offset)
        is_async = any(ch.type == "async" for ch in node.children)
        params = self._extract_ts_params(node, source)

        body = node.child_by_field_name("body")
        calls, refs, renders, provides = (
            self._collect_body_usages(body, source) if body else ([], [], [], [])
        )
        kind = (
            NodeType.FUNCTION.value
            if is_method
            else self._classify_ts_callable(name, renders, body or node)
        )

        result.entities.append(
            RawEntity(
                name=name,
                fqn=fqn,
                kind=kind,
                line_start=l_start,
                line_end=l_end,
                parent_fqn=parent_fqn,
                parameters=params,
                calls=calls,
                references=refs,
                renders=renders,
                provides=provides,
                is_async=is_async,
                is_method=is_method,
                is_exported=is_exported,
            )
        )

    def _unwrap_callable_initializer(self, val_node: tree_sitter.Node) -> tree_sitter.Node | None:
        if val_node.type in ("arrow_function", "function_expression", "function"):
            return val_node
        # unwrap hoc wrappers like memo(...) or forwardRef(...)
        if val_node.type == "call_expression":
            args = val_node.child_by_field_name("arguments")
            if args:
                for ch in args.children:
                    if ch.type in ("arrow_function", "function_expression", "function"):
                        return ch
        return None

    def _parse_ts_variable_decl(
        self,
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

            name = self._text(name_node, source)
            l_start, l_end = self._lines(child, line_offset)

            if val_node:
                # check commonjs require('...')
                if val_node.type == "call_expression":
                    fn_n = val_node.child_by_field_name("function")
                    if fn_n and self._text(fn_n, source) == "require":
                        args_n = val_node.child_by_field_name("arguments")
                        if args_n:
                            str_n = next((c for c in args_n.children if c.type == "string"), None)
                            if str_n:
                                spec = _strip_quotes(self._text(str_n, source))
                                result.raw_import_specs.append(
                                    ImportSpec(local_name=name, raw_specifier=spec, is_default=True)
                                )
                                continue

                callable_node = self._unwrap_callable_initializer(val_node)
                if callable_node:
                    is_async = any(ch.type == "async" for ch in callable_node.children)
                    params = self._extract_ts_params(callable_node, source)
                    body = callable_node.child_by_field_name("body") or callable_node
                    calls, refs, renders, provides = self._collect_body_usages(body, source)
                    kind = self._classify_ts_callable(name, renders, body)
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
                            is_async=is_async,
                            is_exported=is_exported,
                        )
                    )
                    continue

            calls, refs, renders, provides = (
                self._collect_body_usages(val_node, source) if val_node else ([], [], [], [])
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
                    is_exported=is_exported,
                )
            )

    def _parse_ts_class(
        self,
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
        name = self._text(name_node, source)
        fqn = f"{scope_fqn}.{name}"
        l_start, l_end = self._lines(node, line_offset)

        decorators = list(extra_decorators or [])
        decorators.extend(c for c in node.children if c.type == "decorator")

        kind = NodeType.CLASS.value
        provides: list[str] = []
        bases: list[str] = []
        injects: list[str] = []

        for dec in decorators:
            dec_text = self._text(dec, source)
            if "@Component" in dec_text:
                kind = NodeType.COMPONENT.value
                self._extract_decorator_assets(dec_text, result)
            elif "@Injectable" in dec_text:
                kind = NodeType.SERVICE.value
            elif "@NgModule" in dec_text or "@Module" in dec_text:
                kind = NodeType.MODULE.value
                provides.extend(self._extract_module_provides(dec))

        # parse inheritance clauses
        for ch in node.children:
            if ch.type == "class_heritage":
                for clause in ch.children:
                    if clause.type in ("extends_clause", "implements_clause"):
                        for sub in clause.children:
                            if sub.type in ("identifier", "type_identifier", "generic_type"):
                                bases.append(self._text(sub, source).split("<")[0])

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
                m_name = self._text(m_name_n, source) if m_name_n else ""
                if m_name == "constructor":
                    di_types = self._extract_constructor_di(member, source)
                    injects.extend(di_types)
                    class_entity.injects.extend(di_types)
                self._parse_ts_function(
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
                    callable_n = self._unwrap_callable_initializer(fval_n)
                    if callable_n:
                        m_name = self._text(fname_n, source)
                        ml_start, ml_end = self._lines(member, line_offset)
                        m_body = callable_n.child_by_field_name("body") or callable_n
                        calls, refs, renders, m_prov = self._collect_body_usages(m_body, source)
                        result.entities.append(
                            RawEntity(
                                name=m_name,
                                fqn=f"{fqn}.{m_name}",
                                kind=NodeType.FUNCTION.value,
                                line_start=ml_start,
                                line_end=ml_end,
                                parent_fqn=fqn,
                                parameters=self._extract_ts_params(callable_n, source),
                                calls=calls,
                                references=refs,
                                renders=renders,
                                provides=m_prov,
                                is_method=True,
                            )
                        )

    def _extract_decorator_assets(self, dec_text: str, result: FileParseResult) -> None:
        # capture angular templateUrl and styleUrls paths
        for m in re.finditer(r"""(?:templateUrl|styleUrls?)\s*:\s*\[?\s*['"]([^'"]+)['"]""", dec_text):
            spec = m.group(1)
            result.raw_import_specs.append(
                ImportSpec(local_name=None, raw_specifier=spec, is_side_effect=True)
            )

    def _extract_module_provides(self, dec_node: tree_sitter.Node) -> list[str]:
        # extract identifiers listed in ngmodule metadata
        refs: list[str] = []
        stack = [dec_node]
        while stack:
            curr = stack.pop()
            if curr.type == "identifier":
                refs.append(curr.text.decode("utf-8", errors="replace") if curr.text else "")
            stack.extend(curr.children)
        return [r for r in refs if r and r[0].isupper()]

    def _extract_constructor_di(
        self, ctor_node: tree_sitter.Node, source: bytes
    ) -> list[str]:
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
                            tname = self._text(sub, source)
                            if tname not in ("string", "number", "boolean", "any", "unknown"):
                                di_types.append(tname)
                        elif sub.type == "generic_type":
                            base_t = sub.child_by_field_name("name")
                            if base_t:
                                di_types.append(self._text(base_t, source))
        return di_types

    def _parse_ts_type_decl(
        self,
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
        name = self._text(name_node, source)
        l_start, l_end = self._lines(node, line_offset)
        bases: list[str] = []
        for ch in node.children:
            if ch.type == "extends_type_clause":
                for sub in ch.children:
                    if sub.type in ("type_identifier", "identifier"):
                        bases.append(self._text(sub, source))

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
