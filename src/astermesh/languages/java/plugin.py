from pathlib import Path
from typing import TYPE_CHECKING

from astermesh.graph.filters import is_java_test_path
from astermesh.languages.base import LanguageLinkStrategy, LanguagePlugin
from astermesh.languages.java.extractor import extract_java
from astermesh.languages.java.linker import JavaLinkStrategy
from astermesh.parser.types import FileParseResult

if TYPE_CHECKING:
    import tree_sitter


class JavaLanguagePlugin(LanguagePlugin):
    """Java 8-21 Tree-sitter plugin."""

    language_id = "java"
    language_family = "java"
    extensions = frozenset({".java"})
    ignore_dirs = frozenset({
        "target",
        ".gradle",
        ".idea",
        ".mvn",
        "generated-sources",
        "generated-test-sources",
    })
    config_watch_files = frozenset({
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
    })

    def __init__(self) -> None:
        self._parser: tree_sitter.Parser | None = None
        self._init_attempted = False

    def _get_parser(self) -> "tree_sitter.Parser | None":
        if self._init_attempted:
            return self._parser
        self._init_attempted = True
        try:
            import tree_sitter
            import tree_sitter_java as tsjava

            lang = tree_sitter.Language(tsjava.language())
            self._parser = tree_sitter.Parser(lang)
        except Exception:
            self._parser = None
        return self._parser

    def is_available(self) -> bool:
        """Return whether tree-sitter-java is available."""
        return self._get_parser() is not None

    def is_test_path(self, rel_path: str) -> bool:
        """Delegate Java test path matching."""
        return is_java_test_path(rel_path)

    def parse_file(
        self, source_bytes: bytes, rel_path: str, root_path: Path
    ) -> FileParseResult | None:
        """Parse Java source bytes into IR."""
        parser = self._get_parser()
        if parser is None:
            return None
        module_fqn = Path(rel_path).with_suffix("").as_posix().replace("/", ".") or "root"
        result = FileParseResult(
            file_path=rel_path,
            module_fqn=module_fqn,
            language="java",
            language_family="java",
        )
        tree = parser.parse(source_bytes)
        extract_java(tree.root_node, source_bytes, result)
        return result

    def create_link_strategy(self, root_path: Path) -> LanguageLinkStrategy:
        """Create Java pass-2 link strategy."""
        return JavaLinkStrategy()
