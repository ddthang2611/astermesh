import re
from typing import TYPE_CHECKING

from astermesh.graph.model import NodeType

if TYPE_CHECKING:
    from tree_sitter import Node

    from astermesh.parser.types import FileParseResult, RawEntity

LOMBOK_GETTER_ANNOTATIONS: frozenset[str] = frozenset({"Data", "Getter", "Value"})
LOMBOK_SETTER_ANNOTATIONS: frozenset[str] = frozenset({"Data", "Setter"})
LOMBOK_BUILDER_ANNOTATIONS: frozenset[str] = frozenset({"Builder"})

_DI_ANNOTATIONS: frozenset[str] = frozenset({"Autowired", "Inject"})
_IMMUTABLE_ANNOTATIONS: frozenset[str] = frozenset({"Value.Immutable", "Immutable"})
_GUICE_MODULE_BASES: frozenset[str] = frozenset({"AbstractModule", "KillBillModule"})
_BIND_RE = re.compile(
    r"\bbind\s*\(\s*([A-Za-z_][\w.$]*?)\.class\s*\)\s*"
    r"\.(?:to|toProvider)\s*\(\s*([A-Za-z_][\w.$]*?)\.class\s*\)"
)


def simple_annotation_name(ann: str) -> str:
    """Return short or Value.Immutable annotation name."""
    if ann.endswith(".Value.Immutable") or ann == "Value.Immutable":
        return "Value.Immutable"
    return ann.rsplit(".", 1)[-1]


def has_lombok_getter(annotations: list[str]) -> bool:
    """Check if annotations generate Lombok getters."""
    return any(simple_annotation_name(a) in LOMBOK_GETTER_ANNOTATIONS for a in annotations)


def has_lombok_setter(annotations: list[str]) -> bool:
    """Check if annotations generate Lombok setters."""
    return any(simple_annotation_name(a) in LOMBOK_SETTER_ANNOTATIONS for a in annotations)


def has_lombok_builder(annotations: list[str]) -> bool:
    """Check if annotations include Lombok Builder."""
    return any(simple_annotation_name(a) in LOMBOK_BUILDER_ANNOTATIONS for a in annotations)


def is_guice_module(bases: list[str]) -> bool:
    """Check if class extends Guice module."""
    return any(b.rsplit(".", 1)[-1] in _GUICE_MODULE_BASES for b in bases)


def extract_guice_bindings(
    method_node: "Node",
    source_bytes: bytes,
    configure_ent: "RawEntity",
) -> None:
    """Extract Guice bind().to()/toProvider() types."""
    text = source_bytes[method_node.start_byte : method_node.end_byte].decode(
        "utf-8", errors="replace"
    )
    for match in _BIND_RE.finditer(text):
        iface = match.group(1).strip()
        impl = match.group(2).strip()
        for target in (iface, impl):
            if target and target not in configure_ent.provides:
                configure_ent.provides.append(target)


def apply_di_and_codegen(
    parsed: "FileParseResult",
    class_entities: list["RawEntity"],
    uninitialized_final_fields: dict[str, list[str]],
    all_nonstatic_fields: dict[str, list[str]],
    field_annotations: dict[str, dict[str, list[str]]],
    methods_by_owner: dict[str, list["RawEntity"]],
) -> None:
    """Apply Spring, Jakarta, Lombok, Immutables rules."""
    for cls_ent in class_entities:
        ann_names = {simple_annotation_name(a) for a in cls_ent.annotations}
        is_di_bean = cls_ent.kind in {NodeType.SERVICE.value, NodeType.MODULE.value}

        if ann_names & _IMMUTABLE_ANNOTATIONS:
            imm_short = f"Immutable{cls_ent.name}"
            parsed.type_aliases[imm_short] = cls_ent.fqn
            if cls_ent.namespace_fqn:
                parsed.type_aliases[f"{cls_ent.namespace_fqn}.{imm_short}"] = cls_ent.fqn

        if is_di_bean:
            cls_field_anns = field_annotations.get(cls_ent.fqn, {})
            for fname, ftype in cls_ent.field_types.items():
                f_anns = {simple_annotation_name(a) for a in cls_field_anns.get(fname, [])}
                if f_anns & _DI_ANNOTATIONS and ftype not in cls_ent.injects:
                    cls_ent.injects.append(ftype)

            constructors = [m for m in methods_by_owner.get(cls_ent.fqn, []) if m.name == "$init"]
            for m_ent in constructors:
                m_anns = {simple_annotation_name(a) for a in m_ent.annotations}
                if (m_anns & _DI_ANNOTATIONS) or (
                    cls_ent.kind == NodeType.SERVICE.value and len(constructors) == 1
                ):
                    for ptype in m_ent.param_types:
                        if ptype and ptype not in cls_ent.injects:
                            cls_ent.injects.append(ptype)

        if "RequiredArgsConstructor" in ann_names:
            for ftype in uninitialized_final_fields.get(cls_ent.fqn, []):
                if ftype and ftype not in cls_ent.injects:
                    cls_ent.injects.append(ftype)

        if is_di_bean and "AllArgsConstructor" in ann_names:
            for ftype in all_nonstatic_fields.get(cls_ent.fqn, []):
                if ftype and ftype not in cls_ent.injects:
                    cls_ent.injects.append(ftype)
