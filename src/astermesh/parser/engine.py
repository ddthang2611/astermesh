from pathlib import Path

import tree_sitter
import tree_sitter_javascript as tsjavascript
import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript

from astermesh.parser.extractors import extract_python, extract_ts_js, parse_sfc
from astermesh.parser.types import FileParseResult, ImportSpec, RawEntity

__all__ = ["ASTEngine", "FileParseResult", "ImportSpec", "RawEntity"]


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
            return parse_sfc(source_bytes, rel_path, module_fqn, ext, self._parsers)

        parser = self._parsers[ext]
        tree = parser.parse(source_bytes)
        lang_name = "python" if ext == ".py" else ("typescript" if "ts" in ext else "javascript")
        result = FileParseResult(file_path=rel_path, module_fqn=module_fqn, language=lang_name)

        if ext == ".py":
            extract_python(tree.root_node, source_bytes, module_fqn, result)
        else:
            extract_ts_js(tree.root_node, source_bytes, module_fqn, result)
        return result

    def _path_to_module(self, rel_path: str) -> str:
        p = Path(rel_path)
        parts = list(p.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts) if parts else "root"
