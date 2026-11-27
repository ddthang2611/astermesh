import json
import os
import posixpath
import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from astermesh.graph.filters import is_ignored_dir, is_saved_webpage_dir

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

_JS_TO_TS_EXTS: dict[str, tuple[str, ...]] = {
    ".js": (".ts", ".tsx"),
    ".jsx": (".tsx", ".ts"),
    ".mjs": (".mts",),
    ".cjs": (".cts",),
}

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


# per-subroot tsconfig/jsconfig state
@dataclass
class _SubRootConfig:
    sub_rel: str
    trie_root: _TrieNode
    has_aliases: bool
    base_url: str | None


# js/ts/bundler module and path alias resolver
class ModuleResolver:
    def __init__(self, root_path: Path, max_cache: int = 4096) -> None:
        self.root_path = root_path.resolve()
        self._trie_root = _TrieNode()
        self._has_aliases = False
        self._base_url: str | None = None
        self._sub_configs: list[_SubRootConfig] = []
        self._known_files: set[str] = set()
        self._dir_entries: dict[str, frozenset[str] | None] = {}
        self._max_cache = max_cache
        self._cache: OrderedDict[tuple[str, str], str | None] = OrderedDict()
        self._load_tsconfig()

    def set_known_files(self, files: set[str]) -> None:
        self._known_files = files
        self._dir_entries.clear()
        self._cache.clear()

    def reload_config(self) -> None:
        self._trie_root = _TrieNode()
        self._has_aliases = False
        self._base_url = None
        self._sub_configs = []
        self._dir_entries.clear()
        self._load_tsconfig()
        self._cache.clear()

    def _anchor_base_url(self, sub_rel: str, raw_base: str) -> str:
        cleaned = raw_base.replace("\\", "/").strip()
        if cleaned in ("", ".", "./", "/"):
            joined = sub_rel or "."
        else:
            rel_base = cleaned.lstrip("/")
            joined = posixpath.join(sub_rel, rel_base) if sub_rel else rel_base
        norm = posixpath.normpath(joined)
        return "" if norm == "." else norm

    def _parse_dir_config(self, cfg_dir: Path, sub_rel: str) -> _SubRootConfig | None:
        for cfg_name in ("tsconfig.json", "jsconfig.json"):
            cfg_path = cfg_dir / cfg_name
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

            trie_root = _TrieNode()
            has_aliases = False
            base_url: str | None = None

            raw_base = opts.get("baseUrl")
            if isinstance(raw_base, str):
                base_url = self._anchor_base_url(sub_rel, raw_base)

            paths = opts.get("paths")
            if isinstance(paths, dict):
                for pattern, targets in paths.items():
                    if isinstance(pattern, str) and isinstance(targets, list):
                        str_targets = [t for t in targets if isinstance(t, str)]
                        if str_targets:
                            self._insert_alias(pattern, str_targets, trie_root)
                            has_aliases = True
            return _SubRootConfig(
                sub_rel=sub_rel,
                trie_root=trie_root,
                has_aliases=has_aliases,
                base_url=base_url,
            )
        return None

    def _load_tsconfig(self) -> None:
        for dirpath, dirnames, filenames in os.walk(self.root_path):
            curr_dir = Path(dirpath)
            try:
                rel_dir = curr_dir.relative_to(self.root_path).as_posix()
            except ValueError:
                continue
            sibling_lower = {f.lower() for f in filenames}
            kept_dirs: list[str] = []
            for d in sorted(dirnames):
                if is_ignored_dir(d) or is_saved_webpage_dir(d, sibling_lower):
                    continue
                kept_dirs.append(d)
            dirnames[:] = kept_dirs

            sub_rel = "" if rel_dir == "." else rel_dir
            cfg = self._parse_dir_config(curr_dir, sub_rel)
            if cfg is not None:
                if sub_rel == "":
                    self._trie_root = cfg.trie_root
                    self._base_url = cfg.base_url
                if cfg.has_aliases:
                    self._has_aliases = True
                if cfg.has_aliases or cfg.base_url is not None:
                    self._sub_configs.append(cfg)

        self._sub_configs.sort(key=lambda c: (-len(c.sub_rel), c.sub_rel))

    def _insert_alias(
        self, pattern: str, targets: list[str], trie_root: _TrieNode | None = None
    ) -> None:
        has_wildcard = "*" in pattern
        prefix = pattern.split("*")[0].rstrip("/")
        segments = [s for s in prefix.split("/") if s]
        node = trie_root if trie_root is not None else self._trie_root
        for seg in segments:
            node = node.children.setdefault(seg, _TrieNode())
        node.rule = (has_wildcard, targets)
        if trie_root is None:
            self._has_aliases = True

    def _find_sub_config(self, source_dir: str) -> _SubRootConfig | None:
        clean_dir = source_dir.strip("/")
        if clean_dir == ".":
            clean_dir = ""
        for cfg in self._sub_configs:
            if cfg.sub_rel == "":
                return cfg
            if clean_dir == cfg.sub_rel or clean_dir.startswith(f"{cfg.sub_rel}/"):
                return cfg
        return None

    def _match_alias(self, r_clean: str, cfg: _SubRootConfig | None = None) -> list[str]:
        trie_root = cfg.trie_root if cfg is not None else self._trie_root
        has_aliases = cfg.has_aliases if cfg is not None else self._has_aliases
        if not has_aliases:
            return []
        segments = [s for s in r_clean.split("/") if s]
        node = trie_root
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
        base_url = cfg.base_url if cfg is not None else self._base_url
        sub_rel = cfg.sub_rel if cfg is not None else ""
        anchor = base_url if base_url is not None else sub_rel

        candidates: list[str] = []
        for t in targets:
            norm_t = t.replace("\\", "/")
            expanded = norm_t.replace("*", suffix) if "*" in norm_t else norm_t
            if expanded.startswith("/"):
                full = expanded
            elif anchor:
                full = posixpath.normpath(f"{anchor}/{expanded}")
            else:
                full = posixpath.normpath(expanded)
            candidates.append(full)
        return candidates

    def _normalize_within(self, rel_posix: str) -> str | None:
        """Lexically validate and normalize workspace-relative POSIX path."""
        if not rel_posix or rel_posix.startswith("/") or "\x00" in rel_posix:
            return None
        norm = posixpath.normpath(rel_posix)
        if norm == ".." or norm.startswith(("../", "/")):
            return None
        return norm

    def _dir_listing(self, parent_rel: str) -> frozenset[str] | None:
        if parent_rel in self._dir_entries:
            return self._dir_entries[parent_rel]
        parent_path = self.root_path if parent_rel in ("", ".") else (self.root_path / parent_rel)
        try:
            entries: frozenset[str] | None = frozenset(os.listdir(parent_path))
        except OSError:
            entries = None
        self._dir_entries[parent_rel] = entries
        return entries

    def _is_within_workspace(self, rel_posix: str) -> bool:
        norm = self._normalize_within(rel_posix)
        if norm is None:
            return False
        if norm in self._known_files:
            return True
        cand = self.root_path / norm
        if not cand.exists() and not cand.is_symlink():
            return True
        try:
            return cand.resolve().is_relative_to(self.root_path)
        except OSError:
            return False

    def _file_exists(self, rel_posix: str) -> bool:
        norm = self._normalize_within(rel_posix)
        if not norm or norm == ".":
            return False
        if norm in self._known_files:
            return True
        parent_rel, name = posixpath.split(norm)
        listing = self._dir_listing(parent_rel)
        if listing is None or name not in listing:
            return False
        cand = self.root_path / norm
        if not cand.is_file():
            return False
        try:
            return cand.resolve().is_relative_to(self.root_path)
        except OSError:
            return False

    def _resolve_js_to_ts(self, norm: str, check_disk: bool) -> str | None:
        stem, ext = posixpath.splitext(norm)
        ts_exts = _JS_TO_TS_EXTS.get(ext.lower())
        if not ts_exts:
            return None
        for ts_ext in ts_exts:
            probe = f"{stem}{ts_ext}"
            if probe in self._known_files or (check_disk and self._file_exists(probe)):
                return probe
        return None

    def _probe_candidate(self, cand: str) -> str | None:
        norm = self._normalize_within(cand)
        if not norm:
            return None

        if norm != ".":
            if norm in self._known_files:
                return norm
            # map NodeNext JS imports to TS
            ts_cand = self._resolve_js_to_ts(norm, check_disk=False)
            if ts_cand is not None:
                return ts_cand
            if self._file_exists(norm):
                return norm
            if not self._known_files:
                ts_cand = self._resolve_js_to_ts(norm, check_disk=True)
                if ts_cand is not None:
                    return ts_cand

        if norm != ".":
            for ext in SOURCE_PROBE_EXTS:
                probe = f"{norm}{ext}"
                if self._file_exists(probe):
                    return probe

        prefix = "" if norm == "." else f"{norm}/"
        for ext in SOURCE_PROBE_EXTS:
            probe = f"{prefix}index{ext}"
            if self._file_exists(probe):
                return probe

        if norm != ".":
            for ext in ASSET_EXTENSIONS:
                probe = f"{norm}{ext}"
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

        cfg = self._find_sub_config(source_dir)
        candidates: list[str] = []
        alias_cands = self._match_alias(r_clean, cfg) if cfg is not None else []
        active_base_url = cfg.base_url if cfg is not None else None
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
        elif active_base_url is not None:
            joined = f"{active_base_url}/{r_clean}" if active_base_url else r_clean
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
