from dataclasses import dataclass, field


# static import specification container
@dataclass
class ImportSpec:
    local_name: str | None
    raw_specifier: str
    imported_symbol: str | None = None
    is_default: bool = False
    is_namespace: bool = False
    is_side_effect: bool = False


# raw entity extracted from ast
@dataclass
class RawEntity:
    name: str
    fqn: str
    kind: str
    line_start: int
    line_end: int
    parent_fqn: str | None = None
    parameters: list[str] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    renders: list[str] = field(default_factory=list)
    injects: list[str] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    is_async: bool = False
    is_method: bool = False
    is_exported: bool = False


# file parsing outcome container
@dataclass
class FileParseResult:
    file_path: str
    module_fqn: str
    entities: list[RawEntity] = field(default_factory=list)
    imports: dict[str, str] = field(default_factory=dict)
    raw_import_specs: list[ImportSpec] = field(default_factory=list)
    language: str = "unknown"
