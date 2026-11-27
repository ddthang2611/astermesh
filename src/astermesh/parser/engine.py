from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from astermesh.parser.types import FileParseResult, ImportSpec, RawEntity

if TYPE_CHECKING:
    from astermesh.languages.registry import LanguageRegistry

__all__ = ["ASTEngine", "FileParseResult", "ImportSpec", "RawEntity"]


# multi-language tree-sitter orchestrator
class ASTEngine:
    def __init__(self, registry: LanguageRegistry | None = None) -> None:
        if registry is None:
            from astermesh.languages.registry import default_registry

            registry = default_registry()
        self._registry: LanguageRegistry = registry

    @property
    def supported_extensions(self) -> frozenset[str]:
        return self._registry.supported_extensions

    def supports(self, ext: str) -> bool:
        return self._registry.plugin_for_extension(ext) is not None

    def parse_file(self, file_path: Path, root_path: Path) -> FileParseResult | None:
        plugin = self._registry.plugin_for_extension(file_path.suffix.lower())
        if plugin is None:
            return None

        try:
            source_bytes = file_path.read_bytes()
        except OSError:
            return None

        rel_path = file_path.resolve().relative_to(root_path.resolve()).as_posix()
        return plugin.parse_file(source_bytes, rel_path, root_path)

    def _path_to_module(self, rel_path: str) -> str:
        p = Path(rel_path)
        parts = list(p.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts) if parts else "root"
