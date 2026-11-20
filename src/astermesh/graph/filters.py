import fnmatch
import re
from collections.abc import Sequence
from pathlib import Path

IGNORE_DIRS = frozenset({
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".agents",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    "coverage",
    ".next",
    ".nuxt",
    "xml-graph",
    "astermesh-graph",
    # codegen output (graphql-codegen, relay, prisma, openapi, ...)
    "generated",
    "__generated__",
})

IGNORED_FILE_SUFFIXES = (
    ".min.js",
    ".min.css",
    ".bundle.js",
    ".iife.js",
    ".umd.js",
    ".esm.min.js",
    ".map",
    ".generated.ts",
    ".generated.tsx",
    ".generated.js",
    ".generated.jsx",
)

_DUPLICATE_DOWNLOAD_RE = re.compile(r"\(\d+\)\.[cm]?js$")
_HASHED_JS_RE = re.compile(r"\.[0-9a-f]{16,}\.[cm]?js$")
_JS_MINIFY_CHECK_EXTS = frozenset({".js", ".mjs", ".cjs"})
_MAX_UNMINIFIED_JS_BYTES = 512 * 1024
_JS_MINIFY_SAMPLE_BYTES = 8192

# folders whose json/yaml are test data, not project configuration
TEST_DATA_DIRS = frozenset({
    "test",
    "tests",
    "__tests__",
    "fixtures",
    "__fixtures__",
    "mocks",
    "__mocks__",
    "__snapshots__",
    "testdata",
})

_TEST_FILE_RE = re.compile(
    r"(^|/)(test_[^/]+\.py|[^/]+_test\.py|conftest\.py|[^/]+\.(test|spec)\.[cm]?[jt]sx?)$"
)


def is_test_path(rel_path: str) -> bool:
    """Return True if a workspace-relative path looks like a test file or test fixture."""
    rel = rel_path.replace("\\", "/")
    if _TEST_FILE_RE.search(rel):
        return True
    return any(p in TEST_DATA_DIRS for p in rel.split("/")[:-1])


def is_ignored_dir(path: Path | str) -> bool:
    """Return True if any segment of the path is an ignored or hidden directory."""
    parts = Path(path).parts
    for p in parts:
        if p in IGNORE_DIRS or p.endswith(".egg-info"):
            return True
        if p.startswith(".") and p not in {".", ".."}:
            return True
    return False


def is_ignored_file(path: Path | str) -> bool:
    """Return True if the filename matches an ignored build/codegen suffix."""
    lower = str(path).lower()
    if lower.endswith(IGNORED_FILE_SUFFIXES):
        return True
    name = Path(lower).name
    return bool(_DUPLICATE_DOWNLOAD_RE.search(name) or _HASHED_JS_RE.search(name))


def is_saved_webpage_dir(dir_name: str, sibling_files: set[str]) -> bool:
    """Return True for `<stem>_files` directories paired with `<stem>.html`."""
    lower_dir = dir_name.lower()
    if not lower_dir.endswith("_files"):
        return False
    stem = lower_dir[:-6]
    if not stem:
        return False
    siblings_lower = {f.lower() for f in sibling_files}
    return f"{stem}.html" in siblings_lower or f"{stem}.htm" in siblings_lower


def is_minified_js(fpath: Path) -> bool:
    """Detect minified JS files by size and line length metrics."""
    if fpath.suffix.lower() not in _JS_MINIFY_CHECK_EXTS:
        return False
    try:
        if fpath.stat().st_size > _MAX_UNMINIFIED_JS_BYTES:
            return True
        with fpath.open("rb") as fh:
            sample = fh.read(_JS_MINIFY_SAMPLE_BYTES)
    except OSError:
        return False
    if not sample:
        return False
    text = sample.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if not lines:
        return False
    max_line = max(len(ln) for ln in lines)
    avg_line = len(text) / len(lines)
    return max_line > 1000 and avg_line > 200


def load_gitignore_patterns(root_path: Path) -> list[str]:
    """Load basic ignore patterns from root .gitignore."""
    gi_path = root_path / ".gitignore"
    if not gi_path.is_file():
        return []
    try:
        raw_lines = gi_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    patterns: list[str] = []
    for raw in raw_lines:
        line = raw.strip()
        if not line or line.startswith(("#", "!")):
            continue
        patterns.append(line)
    return patterns


def matches_exclude_patterns(
    rel_posix: str, patterns: Sequence[str], is_dir: bool = False
) -> bool:
    """Match workspace-relative path against glob and prefix patterns."""
    rel = rel_posix.replace("\\", "/").strip("/")
    if not rel or rel == ".":
        return False
    parts = rel.split("/")

    for raw in patterns:
        pat = raw.strip().replace("\\", "/").lstrip("/")
        if not pat:
            continue
        dir_only = pat.endswith("/")
        pat = pat.rstrip("/")
        if not pat:
            continue
        if pat.endswith("/**"):
            base = pat[:-3]
            if rel == base or rel.startswith(f"{base}/") or fnmatch.fnmatch(rel, pat):
                return True
            continue
        if "/" not in pat:
            segs = parts[:-1] if (dir_only and not is_dir) else parts
            if any(fnmatch.fnmatch(seg, pat) for seg in segs):
                return True
            continue
        if dir_only:
            prefixes = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
            if any(p == pat or fnmatch.fnmatch(p, pat) for p in prefixes):
                return True
        elif rel == pat or rel.startswith(f"{pat}/") or fnmatch.fnmatch(rel, pat):
            return True
    return False
