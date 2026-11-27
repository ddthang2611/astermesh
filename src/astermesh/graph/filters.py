import fnmatch
import re
from collections.abc import Sequence
from functools import lru_cache
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
    "-min.js",
    ".min.css",
    ".bundle.js",
    "-bundle.js",
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
_SWAGGER_UI_RE = re.compile(r"swagger-ui.*\.js$", re.IGNORECASE)
_WEBAPP_LIB_RE = re.compile(r"(^|/)webapp/lib/", re.IGNORECASE)
_VENDOR_JS_EXTS = (
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".mts",
    ".cts",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".map",
)
_JAVA_TEST_FILE_SUFFIXES = (
    "Test.java",
    "Tests.java",
    "IT.java",
    "Spec.java",
    "TestBase.java",
    "Suite.java",
    "SuiteNoDB.java",
)
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


@lru_cache(maxsize=16384)
def is_test_path(rel_path: str) -> bool:
    """Return True if a workspace-relative path looks like a test file or test fixture."""
    rel = rel_path.replace("\\", "/")
    if _TEST_FILE_RE.search(rel):
        return True
    for seg in rel.split("/")[:-1]:
        if seg in TEST_DATA_DIRS:
            return True
        lower_seg = seg.lower()
        if lower_seg.endswith("-e2e") or lower_seg.startswith("e2e-"):
            return True
    return False


def is_vendor_js_path(rel_path: str) -> bool:
    """Match vendor JS/TS/CSS inside webapp/lib."""
    rel = rel_path.replace("\\", "/")
    if not _WEBAPP_LIB_RE.search(rel):
        return False
    return rel.lower().endswith(_VENDOR_JS_EXTS)


@lru_cache(maxsize=16384)
def is_java_test_path(rel_path: str) -> bool:
    """Match Java test paths and files."""
    rel = rel_path.replace("\\", "/").lstrip("/")
    if not rel:
        return False
    if rel.startswith("src/test/") or "/src/test/" in rel:
        return True
    if rel.startswith("integration-tests/") or "/integration-tests/" in rel:
        return True
    parts = rel.split("/")
    dir_parts = parts[:-1]
    if "e2e-tests" in "/".join(dir_parts).lower():
        return True
    if any(seg.lower().endswith(("-test", "-tests")) for seg in dir_parts):
        return True
    return parts[-1].endswith(_JAVA_TEST_FILE_SUFFIXES)


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
    name = Path(lower.replace("\\", "/")).name
    return bool(
        _DUPLICATE_DOWNLOAD_RE.search(name)
        or _HASHED_JS_RE.search(name)
        or _SWAGGER_UI_RE.search(name)
    )


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
