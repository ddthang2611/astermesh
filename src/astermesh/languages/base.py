from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

from astermesh.graph.model import EdgeType
from astermesh.graph.symbol_index import SymbolIndex
from astermesh.parser.resolver import ModuleResolver
from astermesh.parser.types import FileParseResult


# shared context for link strategies
@dataclass(slots=True)
class LinkContext:
    graph: nx.MultiDiGraph
    index: SymbolIndex
    resolver: ModuleResolver
    ensure_asset_fn: Callable[[str], str]
    add_edge_fn: Callable[[str, str, EdgeType], None]
    add_entity_edge_fn: Callable[[str, str | None, EdgeType], None]
    is_test_path_fn: Callable[[str], bool]


# language-specific edge linking contract
class LanguageLinkStrategy(ABC):
    """Abstract pass-2 file linking strategy."""

    @abstractmethod
    def link_file(self, parsed: FileParseResult, ctx: LinkContext) -> None:
        """Link relations for one file."""

    def invalidate(self) -> None:
        """Reset cached strategy state."""
        return


# language plugin interface
class LanguagePlugin(ABC):
    """Abstract language discovery and parsing contract."""

    language_id: str = ""
    language_family: str = ""
    extensions: frozenset[str] = frozenset()
    ignore_dirs: frozenset[str] = frozenset()
    config_watch_files: frozenset[str] = frozenset()

    @abstractmethod
    def is_available(self) -> bool:
        """Return whether grammar dependencies exist."""

    def is_test_path(self, rel_path: str) -> bool:
        """Return whether relative path is test."""
        return False

    def should_index(self, fpath: Path, root_path: Path, rel_path: str) -> bool:
        """Return whether file should be indexed."""
        return True

    def on_file_removed(self, fpath: Path, root_path: Path) -> list[Path]:
        """Return sibling paths needing re-index."""
        return []

    @abstractmethod
    def parse_file(
        self, source_bytes: bytes, rel_path: str, root_path: Path
    ) -> FileParseResult | None:
        """Parse source bytes into IR."""

    @abstractmethod
    def create_link_strategy(self, root_path: Path) -> LanguageLinkStrategy:
        """Build pass-2 linking strategy."""
