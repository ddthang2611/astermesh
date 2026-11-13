import re
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
    # codegen output (graphql-codegen, relay, prisma, openapi, ...)
    "generated",
    "__generated__",
})

IGNORED_FILE_SUFFIXES = (
    ".min.js",
    ".min.css",
    ".bundle.js",
    ".map",
    ".generated.ts",
    ".generated.tsx",
    ".generated.js",
    ".generated.jsx",
)

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
    return lower.endswith(IGNORED_FILE_SUFFIXES)
