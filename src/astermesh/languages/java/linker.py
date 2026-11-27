from collections import deque
from collections.abc import Sequence

from astermesh.graph.filters import is_java_test_path
from astermesh.graph.model import EdgeType, NodeType
from astermesh.languages.base import LanguageLinkStrategy, LinkContext
from astermesh.languages.java.di_and_codegen import (
    has_lombok_builder,
    has_lombok_getter,
    has_lombok_setter,
    simple_annotation_name,
)
from astermesh.languages.java.extractor import strip_type_base
from astermesh.parser.types import CallSite, FileParseResult, RawEntity

_JAVA_PRIMITIVES = frozenset({
    "void",
    "boolean",
    "byte",
    "short",
    "int",
    "long",
    "char",
    "float",
    "double",
    "var",
    "String",
    "Object",
    "Integer",
    "Long",
    "Boolean",
    "Double",
    "Float",
    "Byte",
    "Short",
    "Character",
    "Void",
})
_MAX_INHERIT_HOPS = 6
_MAX_CHAIN_HOPS = 3


class JavaLinkStrategy(LanguageLinkStrategy):
    """Java pass-2 linker isolated to java family."""

    def __init__(self) -> None:
        self._entity_cache: dict[str, tuple[RawEntity, FileParseResult] | None] = {}
        self._ancestors_cache: dict[tuple[str, bool, bool], list[str]] = {}
        self._test_cache: dict[str, bool] = {}

    def invalidate(self) -> None:
        """Reset cached entity and hierarchy lookups."""
        self._entity_cache.clear()
        self._ancestors_cache.clear()
        self._test_cache.clear()

    def link_file(self, parsed: FileParseResult, ctx: LinkContext) -> None:
        """Link Java imports, inheritance, DI, and calls."""
        source_is_test = self._is_test(parsed.file_path, ctx)
        self._link_file_imports(parsed, ctx, source_is_test)

        owners_by_fqn: dict[str, RawEntity] = {
            ent.fqn: ent
            for ent in parsed.entities
            if ent.kind in {NodeType.CLASS.value, NodeType.SERVICE.value, NodeType.MODULE.value}
        }

        for ent in parsed.entities:
            owner_fqn = ent.parent_fqn if ent.parent_fqn in owners_by_fqn else None
            owner_ent = owners_by_fqn.get(owner_fqn) if owner_fqn else None
            self._link_entity(ent, owner_ent, parsed, ctx, source_is_test)

    def _is_test(self, rel_path: str, ctx: LinkContext) -> bool:
        cached = self._test_cache.get(rel_path)
        if cached is not None:
            return cached
        res = ctx.is_test_path_fn(rel_path) or is_java_test_path(rel_path)
        self._test_cache[rel_path] = res
        return res

    def _is_allowed_target(
        self, target_fqn: str, source_is_test: bool, ctx: LinkContext
    ) -> bool:
        if not ctx.graph.has_node(target_fqn):
            return False
        node = ctx.graph.nodes[target_fqn]
        fam = node.get("language_family") or ""
        if fam and fam != "java":
            return False
        if target_fqn not in ctx.index.get_symbol_table("java"):
            return False
        if not source_is_test:
            target_file = node.get("file_path")
            if isinstance(target_file, str) and self._is_test(target_file, ctx):
                return False
        return True

    def _lookup_entity(
        self, fqn: str, ctx: LinkContext
    ) -> tuple[RawEntity, FileParseResult] | None:
        if fqn in self._entity_cache:
            return self._entity_cache[fqn]
        if not ctx.graph.has_node(fqn):
            self._entity_cache[fqn] = None
            return None
        fpath = ctx.graph.nodes[fqn].get("file_path")
        if not isinstance(fpath, str):
            self._entity_cache[fqn] = None
            return None
        fp = ctx.index.parsed_files.get(fpath)
        if fp is None:
            self._entity_cache[fqn] = None
            return None
        for ent in fp.entities:
            self._entity_cache[ent.fqn] = (ent, fp)
        return self._entity_cache.get(fqn)

    def _lookup_alias(
        self, name: str, parsed: FileParseResult, ctx: LinkContext, source_is_test: bool
    ) -> str | None:
        target = parsed.type_aliases.get(name) or ctx.index.get_type_alias("java", name)
        if target and self._is_allowed_target(target, source_is_test, ctx):
            return target
        return None

    def resolve_type(
        self,
        raw_type: str,
        parsed: FileParseResult,
        ctx: LinkContext,
        owner_fqn: str | None = None,
        source_is_test: bool = False,
    ) -> str | None:
        """Resolve Java type strictly within java family."""
        cleaned = strip_type_base(raw_type)
        if not cleaned or cleaned.startswith("__ret__:") or cleaned in _JAVA_PRIMITIVES:
            return None

        java_symtab = ctx.index.get_symbol_table("java")

        # 1. enclosing inner classes
        curr = owner_fqn or ""
        while curr and curr != parsed.module_fqn:
            cand = f"{curr}.{cleaned}"
            if cand in java_symtab and self._is_allowed_target(cand, source_is_test, ctx):
                return cand
            if "." not in curr:
                break
            curr = curr.rsplit(".", 1)[0]

        # 2. immutables / type aliases
        alias_hit = self._lookup_alias(cleaned, parsed, ctx, source_is_test)
        if alias_hit:
            return alias_hit

        # 3. explicit imports
        head, sep, rest = cleaned.partition(".")
        if head in parsed.imports:
            imported_base = parsed.imports[head]
            cand = f"{imported_base}{sep}{rest}" if sep else imported_base
            if cand in java_symtab and self._is_allowed_target(cand, source_is_test, ctx):
                return cand
            alias_hit = self._lookup_alias(cand, parsed, ctx, source_is_test)
            if alias_hit:
                return alias_hit

        # 4. same-package namespace members
        if parsed.namespace_fqn:
            for member_fqn in sorted(
                ctx.index.get_namespace_members("java", parsed.namespace_fqn, head)
            ):
                cand = f"{member_fqn}{sep}{rest}" if sep else member_fqn
                if cand in java_symtab and self._is_allowed_target(cand, source_is_test, ctx):
                    return cand
            pkg_cand = f"{parsed.namespace_fqn}.{cleaned}"
            if pkg_cand in java_symtab and self._is_allowed_target(pkg_cand, source_is_test, ctx):
                return pkg_cand
            alias_hit = self._lookup_alias(pkg_cand, parsed, ctx, source_is_test)
            if alias_hit:
                return alias_hit

        # 5. wildcard package imports
        for pkg in parsed.wildcard_imports:
            for member_fqn in sorted(ctx.index.get_namespace_members("java", pkg, head)):
                cand = f"{member_fqn}{sep}{rest}" if sep else member_fqn
                if cand in java_symtab and self._is_allowed_target(cand, source_is_test, ctx):
                    return cand
            pkg_cand = f"{pkg}.{cleaned}"
            if pkg_cand in java_symtab and self._is_allowed_target(pkg_cand, source_is_test, ctx):
                return pkg_cand
            alias_hit = self._lookup_alias(pkg_cand, parsed, ctx, source_is_test)
            if alias_hit:
                return alias_hit

        # 6. fully qualified name
        if cleaned in java_symtab and self._is_allowed_target(cleaned, source_is_test, ctx):
            return cleaned
        return None

    def _link_file_imports(
        self, parsed: FileParseResult, ctx: LinkContext, source_is_test: bool
    ) -> None:
        seen_files: set[str] = set()

        def _add_file_import(type_name: str) -> None:
            target_fqn = self.resolve_type(type_name, parsed, ctx, source_is_test=source_is_test)
            if not target_fqn:
                return
            tfile = ctx.graph.nodes[target_fqn].get("file_path")
            if (
                isinstance(tfile, str)
                and tfile != parsed.file_path
                and tfile not in seen_files
                and ctx.graph.has_node(tfile)
            ):
                ctx.add_edge_fn(parsed.file_path, tfile, EdgeType.IMPORTS)
                seen_files.add(tfile)

        for imp_fqn in parsed.imports.values():
            _add_file_import(imp_fqn)
        for static_fqn in parsed.static_imports.values():
            if "." in static_fqn:
                _add_file_import(static_fqn.rsplit(".", 1)[0])
        for static_cls in parsed.static_wildcards:
            _add_file_import(static_cls)

    def _iter_type_ancestors(
        self,
        start_fqn: str,
        ctx: LinkContext,
        source_is_test: bool,
        include_self: bool = True,
    ) -> list[str]:
        cache_key = (start_fqn, source_is_test, include_self)
        cached = self._ancestors_cache.get(cache_key)
        if cached is not None:
            return cached

        visited: set[str] = {start_fqn}
        order: list[str] = [start_fqn] if include_self else []
        queue: deque[tuple[str, int]] = deque([(start_fqn, 0)])

        while queue:
            curr_fqn, depth = queue.popleft()
            if depth >= _MAX_INHERIT_HOPS:
                continue
            next_fqns: list[str] = []

            if ctx.graph.has_node(curr_fqn):
                for _, dst, data in ctx.graph.out_edges(curr_fqn, data=True):
                    if data.get("type") == EdgeType.INHERITS.value and self._is_allowed_target(
                        dst, source_is_test, ctx
                    ):
                        next_fqns.append(dst)

            raw_bases = ctx.index.type_hierarchy.get(curr_fqn, set())
            if raw_bases:
                found = self._lookup_entity(curr_fqn, ctx)
                curr_parsed = found[1] if found else None
                if curr_parsed is not None:
                    curr_test = self._is_test(curr_parsed.file_path, ctx)
                    for base_name in sorted(raw_bases):
                        resolved = self.resolve_type(
                            base_name,
                            curr_parsed,
                            ctx,
                            owner_fqn=curr_fqn,
                            source_is_test=curr_test,
                        )
                        if resolved and self._is_allowed_target(resolved, source_is_test, ctx):
                            next_fqns.append(resolved)

            for nxt in next_fqns:
                if nxt not in visited:
                    visited.add(nxt)
                    order.append(nxt)
                    queue.append((nxt, depth + 1))

        self._ancestors_cache[cache_key] = order
        return order

    def _resolve_method_on_type(
        self,
        owner_fqn: str,
        method_name: str,
        arity: int,
        arg_types: Sequence[str],
        ctx: LinkContext,
        source_is_test: bool,
        walk_bases: bool = True,
    ) -> str | None:
        ancestors = (
            self._iter_type_ancestors(owner_fqn, ctx, source_is_test, include_self=True)
            if walk_bases
            else [owner_fqn]
        )
        for cls_fqn in ancestors:
            raw_cands = ctx.index.get_overloads(cls_fqn, method_name)
            cands = [c for c in raw_cands if self._is_allowed_target(c, source_is_test, ctx)]
            if not cands:
                continue
            if arity < 0:
                return cands[0]

            arity_matches: list[tuple[str, RawEntity | None]] = []
            for cand in cands:
                found = self._lookup_entity(cand, ctx)
                cand_ent = found[0] if found else None
                if cand_ent is not None and len(cand_ent.parameters) == arity:
                    arity_matches.append((cand, cand_ent))

            if not arity_matches:
                continue
            if len(arity_matches) == 1 or not arg_types:
                return arity_matches[0][0]

            best_cand = arity_matches[0][0]
            best_score = -1
            for cand, cand_ent in arity_matches:
                if cand_ent is None:
                    continue
                score = 0
                for atype, ptype in zip(arg_types, cand_ent.param_types, strict=False):
                    if atype and ptype:
                        a_simp = atype.rsplit(".", 1)[-1]
                        p_simp = ptype.rsplit(".", 1)[-1]
                        if a_simp == p_simp:
                            score += 1
                if score > best_score:
                    best_score = score
                    best_cand = cand
            return best_cand
        return None

    def _try_lombok_accessor(
        self,
        recv_fqn: str,
        raw_name: str,
        arity: int,
        source_fqn: str,
        ctx: LinkContext,
        source_is_test: bool,
    ) -> str | None:
        field_name: str | None = None
        is_getter = False
        if raw_name.startswith("get") and len(raw_name) > 3 and arity in (0, -1):
            field_name = raw_name[3].lower() + raw_name[4:]
            is_getter = True
        elif raw_name.startswith("is") and len(raw_name) > 2 and arity in (0, -1):
            field_name = raw_name[2].lower() + raw_name[3:]
            is_getter = True
        elif raw_name.startswith("set") and len(raw_name) > 3 and arity in (1, -1):
            field_name = raw_name[3].lower() + raw_name[4:]
            is_getter = False

        if not field_name:
            return None

        for cls_fqn in self._iter_type_ancestors(recv_fqn, ctx, source_is_test):
            found = self._lookup_entity(cls_fqn, ctx)
            if not found:
                continue
            cls_ent, cls_parsed = found
            allowed_lombok = (
                has_lombok_getter(cls_ent.annotations)
                if is_getter
                else has_lombok_setter(cls_ent.annotations)
            )
            if not allowed_lombok:
                continue
            field_fqn = f"{cls_fqn}.{field_name}#field"
            if not self._is_allowed_target(field_fqn, source_is_test, ctx):
                field_fqn = f"{cls_fqn}.{field_name}"
            if self._is_allowed_target(field_fqn, source_is_test, ctx):
                ctx.add_entity_edge_fn(source_fqn, field_fqn, EdgeType.REFERENCES)
            else:
                ctx.add_entity_edge_fn(source_fqn, cls_fqn, EdgeType.REFERENCES)

            if is_getter and field_name in cls_ent.field_types:
                return self.resolve_type(
                    cls_ent.field_types[field_name],
                    cls_parsed,
                    ctx,
                    owner_fqn=cls_fqn,
                    source_is_test=source_is_test,
                )
            return None
        return None

    def _resolve_ret_hint(
        self,
        hint: str,
        ent: RawEntity,
        owner_ent: RawEntity | None,
        parsed: FileParseResult,
        ctx: LinkContext,
        source_is_test: bool,
        depth: int,
    ) -> str | None:
        if depth >= 2 or not hint.startswith("__ret__:"):
            return None
        payload = hint[len("__ret__:") :]
        parts = payload.split("|", 3)
        if len(parts) != 4:
            return None
        recv_str, raw_name, arity_str, chain_str = parts
        try:
            arity = int(arity_str)
        except ValueError:
            return None
        chain: list[tuple[str, int]] = []
        if chain_str:
            for item in chain_str.split(";"):
                if ":" in item:
                    sname, sa = item.rsplit(":", 1)
                    try:
                        chain.append((sname, int(sa)))
                    except ValueError:
                        continue
        cs = CallSite(
            raw_name=raw_name,
            arity=arity,
            receiver_var=recv_str or None,
            chain=tuple(chain),
        )
        return self._resolve_call_site(
            cs,
            ent,
            owner_ent,
            parsed,
            ctx,
            source_is_test,
            emit_edges=False,
            depth=depth + 1,
        )

    def _resolve_receiver_type(
        self,
        recv: str,
        ent: RawEntity,
        owner_ent: RawEntity | None,
        parsed: FileParseResult,
        ctx: LinkContext,
        source_is_test: bool,
        depth: int = 0,
    ) -> str | None:
        owner_fqn = owner_ent.fqn if owner_ent else None
        if recv == "this":
            return owner_fqn
        if recv == "super" and owner_ent:
            ancs = self._iter_type_ancestors(
                owner_ent.fqn, ctx, source_is_test, include_self=False
            )
            return ancs[0] if ancs else None

        name = recv[5:] if recv.startswith("this.") else recv
        if not name:
            return None

        if "." not in name:
            if name in ent.var_types:
                vtype = ent.var_types[name]
                if vtype.startswith("__ret__:"):
                    return self._resolve_ret_hint(
                        vtype, ent, owner_ent, parsed, ctx, source_is_test, depth
                    )
                return self.resolve_type(
                    vtype, parsed, ctx, owner_fqn=owner_fqn, source_is_test=source_is_test
                )
            if owner_ent and name in owner_ent.field_types:
                return self.resolve_type(
                    owner_ent.field_types[name],
                    parsed,
                    ctx,
                    owner_fqn=owner_fqn,
                    source_is_test=source_is_test,
                )
            return self.resolve_type(
                name, parsed, ctx, owner_fqn=owner_fqn, source_is_test=source_is_test
            )

        direct = self.resolve_type(
            name, parsed, ctx, owner_fqn=owner_fqn, source_is_test=source_is_test
        )
        if direct:
            return direct

        head, _, tail = name.partition(".")
        head_type = self._resolve_receiver_type(
            head, ent, owner_ent, parsed, ctx, source_is_test, depth
        )
        if head_type:
            for seg in tail.split("."):
                found = self._lookup_entity(head_type, ctx)
                if not found:
                    return None
                h_ent, h_parsed = found
                if seg in h_ent.field_types:
                    head_type = self.resolve_type(
                        h_ent.field_types[seg],
                        h_parsed,
                        ctx,
                        owner_fqn=head_type,
                        source_is_test=source_is_test,
                    )
                    if not head_type:
                        return None
                else:
                    return None
            return head_type
        return None

    def _dispatch_step(
        self,
        recv_fqn: str,
        raw_name: str,
        arity: int,
        arg_types: Sequence[str],
        raw_receiver: str | None,
        source_fqn: str,
        ctx: LinkContext,
        source_is_test: bool,
        emit_edges: bool,
    ) -> str | None:
        if raw_name == "$init":
            ctor_fqn = self._resolve_method_on_type(
                recv_fqn, "$init", arity, arg_types, ctx, source_is_test, walk_bases=False
            )
            if emit_edges:
                ctx.add_entity_edge_fn(source_fqn, ctor_fqn or recv_fqn, EdgeType.CALLS)
            return recv_fqn

        target_method = self._resolve_method_on_type(
            recv_fqn, raw_name, arity, arg_types, ctx, source_is_test, walk_bases=True
        )
        if target_method:
            if emit_edges:
                ctx.add_entity_edge_fn(source_fqn, target_method, EdgeType.CALLS)
            found = self._lookup_entity(target_method, ctx)
            if found and found[0].return_type:
                m_ent, m_parsed = found
                m_owner = m_ent.parent_fqn
                return self.resolve_type(
                    m_ent.return_type or "",
                    m_parsed,
                    ctx,
                    owner_fqn=m_owner,
                    source_is_test=source_is_test,
                )
            return None

        # lombok @Builder and Immutables builder/of/copyOf
        found_cls = self._lookup_entity(recv_fqn, ctx)
        if raw_name == "builder" and found_cls and has_lombok_builder(found_cls[0].annotations):
            if emit_edges:
                ctx.add_entity_edge_fn(source_fqn, recv_fqn, EdgeType.CALLS)
            return recv_fqn
        if raw_receiver and (
            raw_receiver.rsplit(".", 1)[-1].startswith("Immutable")
            and raw_name in ("builder", "of", "copyOf")
        ):
            if emit_edges:
                ctx.add_entity_edge_fn(source_fqn, recv_fqn, EdgeType.CALLS)
            return recv_fqn

        if emit_edges:
            return self._try_lombok_accessor(
                recv_fqn, raw_name, arity, source_fqn, ctx, source_is_test
            )
        return None

    def _resolve_call_site(
        self,
        cs: CallSite,
        ent: RawEntity,
        owner_ent: RawEntity | None,
        parsed: FileParseResult,
        ctx: LinkContext,
        source_is_test: bool,
        emit_edges: bool = True,
        depth: int = 0,
    ) -> str | None:
        curr_type: str | None = None

        if cs.receiver_var is not None:
            recv_fqn = self._resolve_receiver_type(
                cs.receiver_var, ent, owner_ent, parsed, ctx, source_is_test, depth
            )
            if not recv_fqn:
                return None
            curr_type = self._dispatch_step(
                recv_fqn,
                cs.raw_name,
                cs.arity,
                cs.arg_types,
                cs.receiver_var,
                ent.fqn,
                ctx,
                source_is_test,
                emit_edges,
            )
        else:
            # 1. explicit static import
            static_target = parsed.static_imports.get(cs.raw_name)
            if static_target and "." in static_target:
                cls_name = static_target.rsplit(".", 1)[0]
                cls_fqn = self.resolve_type(cls_name, parsed, ctx, source_is_test=source_is_test)
                if cls_fqn:
                    curr_type = self._dispatch_step(
                        cls_fqn,
                        cs.raw_name,
                        cs.arity,
                        cs.arg_types,
                        cls_name,
                        ent.fqn,
                        ctx,
                        source_is_test,
                        emit_edges,
                    )
                    if not cs.chain:
                        return curr_type

            # 2. wildcard static imports
            if curr_type is None:
                for cls_name in parsed.static_wildcards:
                    cls_fqn = self.resolve_type(
                        cls_name, parsed, ctx, source_is_test=source_is_test
                    )
                    if not cls_fqn:
                        continue
                    m_fqn = self._resolve_method_on_type(
                        cls_fqn, cs.raw_name, cs.arity, cs.arg_types, ctx, source_is_test
                    )
                    if m_fqn:
                        curr_type = self._dispatch_step(
                            cls_fqn,
                            cs.raw_name,
                            cs.arity,
                            cs.arg_types,
                            cls_name,
                            ent.fqn,
                            ctx,
                            source_is_test,
                            emit_edges,
                        )
                        break

            # 3. same-class or inherited method
            if curr_type is None and owner_ent is not None:
                curr_type = self._dispatch_step(
                    owner_ent.fqn,
                    cs.raw_name,
                    cs.arity,
                    cs.arg_types,
                    None,
                    ent.fqn,
                    ctx,
                    source_is_test,
                    emit_edges,
                )

        for step_name, step_arity in cs.chain[:_MAX_CHAIN_HOPS]:
            if not curr_type:
                break
            curr_type = self._dispatch_step(
                curr_type,
                step_name,
                step_arity,
                [],
                None,
                ent.fqn,
                ctx,
                source_is_test,
                emit_edges,
            )
        return curr_type

    def _link_override_edges(
        self,
        ent: RawEntity,
        owner_ent: RawEntity,
        ctx: LinkContext,
        source_is_test: bool,
    ) -> None:
        if not ent.is_method or ent.name == "$init":
            return
        is_override = any(simple_annotation_name(a) == "Override" for a in ent.annotations)
        arity = len(ent.parameters)
        for anc_fqn in self._iter_type_ancestors(
            owner_ent.fqn, ctx, source_is_test, include_self=False
        ):
            raw_cands = ctx.index.get_overloads(anc_fqn, ent.name)
            cands = [c for c in raw_cands if self._is_allowed_target(c, source_is_test, ctx)]
            if not cands:
                continue
            matched = False
            for cand in cands:
                found = self._lookup_entity(cand, ctx)
                cand_ent = found[0] if found else None
                if cand_ent is not None and len(cand_ent.parameters) == arity:
                    ctx.add_entity_edge_fn(ent.fqn, cand, EdgeType.INHERITS)
                    matched = True
            if not matched and is_override and len(cands) == 1:
                ctx.add_entity_edge_fn(ent.fqn, cands[0], EdgeType.INHERITS)

    def _link_entity(
        self,
        ent: RawEntity,
        owner_ent: RawEntity | None,
        parsed: FileParseResult,
        ctx: LinkContext,
        source_is_test: bool,
    ) -> None:
        owner_fqn = owner_ent.fqn if owner_ent else None

        for base in ent.bases:
            target = self.resolve_type(
                base, parsed, ctx, owner_fqn=owner_fqn or ent.fqn, source_is_test=source_is_test
            )
            ctx.add_entity_edge_fn(ent.fqn, target, EdgeType.INHERITS)

        for inj in ent.injects:
            target = self.resolve_type(
                inj, parsed, ctx, owner_fqn=owner_fqn or ent.fqn, source_is_test=source_is_test
            )
            ctx.add_entity_edge_fn(ent.fqn, target, EdgeType.INJECTS)

        for prov in ent.provides:
            target = self.resolve_type(
                prov, parsed, ctx, owner_fqn=owner_fqn or ent.fqn, source_is_test=source_is_test
            )
            ctx.add_entity_edge_fn(ent.fqn, target, EdgeType.PROVIDES)

        if owner_ent is not None:
            self._link_override_edges(ent, owner_ent, ctx, source_is_test)

        for cs in ent.call_sites:
            self._resolve_call_site(cs, ent, owner_ent, parsed, ctx, source_is_test)
