import json
import posixpath
import re
from collections import OrderedDict
from pathlib import Path

SOURCE_PROBE_EXTS: tuple[str, ...] = (
    ".ts",
    ".tsx",
    ".mts",
    ".cts",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".vue",
    ".svelte",
    ".d.ts",
)

ASSET_EXTENSIONS: frozenset[str] = frozenset({
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".ico",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".json",
    ".html",
    ".woff2",
    ".woff",
    ".ttf",
    ".eot",
    ".mp3",
    ".mp4",
    ".wav",
    ".glsl",
})


def is_asset_path(rel_path: str) -> bool:
    lower = rel_path.lower()
    return any(lower.endswith(ext) for ext in ASSET_EXTENSIONS)


def split_bundler_query(raw_specifier: str) -> tuple[str, str | None]:
    for idx, ch in enumerate(raw_specifier):
        if ch in ("?", "#"):
            return raw_specifier[:idx], raw_specifier[idx:]
    return raw_specifier, None


def _strip_jsonc(text: str) -> str:
    # strip comments and trailing commas from tsconfig json
    out: list[str] = []
    in_str = False
    escape = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            i += 1
        else:
            if ch == '"':
                in_str = True
                out.append(ch)
                i += 1
            elif ch == "/" and i + 1 < n and text[i + 1] == "/":
                i += 2
                while i < n and text[i] != "\n":
                    i += 1
            elif ch == "/" and i + 1 < n and text[i + 1] == "*":
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    i += 1
                i += 2
            else:
                out.append(ch)
                i += 1
    cleaned = "".join(out)
    return re.sub(r",\s*([}\]])", r"\1", cleaned)


# segment prefix trie node for alias rules
class _TrieNode:
    def __init__(self) -> None:
        self.children: dict[str, _TrieNode] = {}
        self.rule: tuple[bool, list[str]] | None = None


# js/ts/bundler module and path alias resolver
class ModuleResolver:
    def __init__(self, root_path: Path, max_cache: int = 4096) -> None:
        self.root_path = root_path.resolve()
        self._trie_root = _TrieNode()
        self._has_aliases = False
        self._base_url: str | None = None
        self._known_files: set[str] = set()
        self._max_cache = max_cache
        self._cache: OrderedDict[tuple[str, str], str | None] = OrderedDict()
        self._load_tsconfig()

    def set_known_files(self, files: set[str]) -> None:
        self._known_files = files
        self._cache.clear()

    def reload_config(self) -> None:
        self._trie_root = _TrieNode()
        self._has_aliases = False
        self._base_url = None
        self._load_tsconfig()
        self._cache.clear()

    def _load_tsconfig(self) -> None:
        for cfg_name in ("tsconfig.json", "jsconfig.json"):
            cfg_path = self.root_path / cfg_name
            if not cfg_path.exists() or not cfg_path.is_file():
                continue
            try:
                raw_text = cfg_path.read_text(encoding="utf-8")
                data = json.loads(_strip_jsonc(raw_text))
            except (OSError, ValueError):
                continue

            opts = data.get("compilerOptions")
            if not isinstance(opts, dict):
                continue

            raw_base = opts.get("baseUrl")
            if isinstance(raw_base, str):
                norm_base = posixpath.normpath(raw_base.replace("\\", "/").strip("/"))
                self._base_url = "" if norm_base == "." else norm_base

            paths = opts.get("paths")
            if isinstance(paths, dict):
                for pattern, targets in paths.items():
                    if isinstance(pattern, str) and isinstance(targets, list):
                        str_targets = [t for t in targets if isinstance(t, str)]
                        if str_targets:
                            self._insert_alias(pattern, str_targets)
            break

    def _insert_alias(self, pattern: str, targets: list[str]) -> None:
        has_wildcard = "*" in pattern
        prefix = pattern.split("*")[0].rstrip("/")
        segments = [s for s in prefix.split("/") if s]
        node = self._trie_root
        for seg in segments:
            node = node.children.setdefault(seg, _TrieNode())
        node.rule = (has_wildcard, targets)
        self._has_aliases = True

    def _match_alias(self, r_clean: str) -> list[str]:
        if not self._has_aliases:
            return []
        segments = [s for s in r_clean.split("/") if s]
        node = self._trie_root
        best_match: tuple[int, tuple[bool, list[str]]] | None = None

        if node.rule is not None:
            best_match = (0, node.rule)

        for idx, seg in enumerate(segments):
            child = node.children.get(seg)
            if child is None:
                break
            node = child
            if node.rule is not None:
                best_match = (idx + 1, node.rule)

        if not best_match:
            return []

        matched_len, (has_wildcard, targets) = best_match
        if not has_wildcard and matched_len != len(segments):
            return []

        suffix = "/".join(segments[matched_len:])
        candidates: list[str] = []
        base_prefix = f"{self._base_url}/" if self._base_url else ""
        for t in targets:
            norm_t = t.replace("\\", "/")
            expanded = norm_t.replace("*", suffix) if "*" in norm_t else norm_t
            full = posixpath.normpath(f"{base_prefix}{expanded.lstrip('./')}")
            candidates.append(full)
        return candidates

    def _is_within_workspace(self, rel_posix: str) -> bool:
        if rel_posix.startswith(("..", "/")):
            return False
        try:
            target = (self.root_path / rel_posix).resolve()
            return target.is_relative_to(self.root_path)
        except OSError:
            return False

    def _file_exists(self, rel_posix: str) -> bool:
        if rel_posix in self._known_files:
            return True
        if not self._is_within_workspace(rel_posix):
            return False
        cand = self.root_path / rel_posix
        return cand.exists() and cand.is_file()

    def _probe_candidate(self, cand: str) -> str | None:
        if not cand or not self._is_within_workspace(cand):
            return None

        if self._file_exists(cand):
            return cand

        for ext in SOURCE_PROBE_EXTS:
            probe = f"{cand}{ext}"
            if self._file_exists(probe):
                return probe

        for ext in SOURCE_PROBE_EXTS:
            probe = f"{cand}/index{ext}"
            if self._file_exists(probe):
                return probe

        for ext in ASSET_EXTENSIONS:
            probe = f"{cand}{ext}"
            if self._file_exists(probe):
                return probe

        return None

    def _resolve_clean(self, source_dir: str, r_clean: str) -> str | None:
        if not r_clean:
            return None

        cache_key = (source_dir, r_clean)
        if cache_key in self._cache:
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key]

        candidates: list[str] = []
        alias_cands = self._match_alias(r_clean)
        if alias_cands:
            candidates.extend(alias_cands)
        elif r_clean.startswith(("./", "../")) or r_clean in (".", ".."):
            base_dir = "" if source_dir in ("", ".") else source_dir
            joined = posixpath.join(base_dir, r_clean) if base_dir else r_clean
            candidates.append(posixpath.normpath(joined))
        elif r_clean.startswith("/"):
            stripped = r_clean.lstrip("/")
            candidates.append(posixpath.normpath(stripped))
            candidates.append(posixpath.normpath(f"public/{stripped}"))
        elif self._base_url is not None:
            joined = f"{self._base_url}/{r_clean}" if self._base_url else r_clean
            candidates.append(posixpath.normpath(joined))
        else:
            self._store_cache(cache_key, None)
            return None

        resolved_result: str | None = None
        for cand in candidates:
            resolved = self._probe_candidate(cand)
            if resolved is not None:
                resolved_result = resolved
                break

        self._store_cache(cache_key, resolved_result)
        return resolved_result

    def _store_cache(self, key: tuple[str, str], value: str | None) -> None:
        self._cache[key] = value
        if len(self._cache) > self._max_cache:
            self._cache.popitem(last=False)

    def resolve(self, source_file_rel: str, raw_specifier: str) -> tuple[str | None, str | None]:
        r_clean, query_flag = split_bundler_query(raw_specifier.strip())
        source_dir = posixpath.dirname(source_file_rel.replace("\\", "/"))
        resolved_path = self._resolve_clean(source_dir, r_clean)
        return resolved_path, query_flag
