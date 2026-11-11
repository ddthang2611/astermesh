import re
from pathlib import Path

from astermesh.graph.model import NodeType
from astermesh.parser.types import FileParseResult, RawEntity

SATELLITE_EXTENSIONS: frozenset[str] = frozenset({
    ".toml",
    ".json",
    ".yaml",
    ".yml",
    ".md",
    ".ini",
    ".cfg",
    ".rst",
})

SATELLITE_FILENAMES: frozenset[str] = frozenset({
    "dockerfile",
    "makefile",
    "containerfile",
    "license",
    "license.txt",
    "license.md",
    "requirements.txt",
})

IGNORED_SATELLITES: frozenset[str] = frozenset({
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "cargo.lock",
    "uv.lock",
    "poetry.lock",
})

_TOML_SECTION_RE = re.compile(r"^\s*\[+([^\[\]]+)\]+\s*$")
_TOML_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*=")
_JSON_KEY_RE = re.compile(r'^(\s{1,4}|\t)"([^"]+)"\s*:')
_MD_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
_YAML_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*:")
_MAKE_TARGET_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*:")


# parser for configuration manifests and documentation files
class SatelliteParser:
    @property
    def supported_extensions(self) -> frozenset[str]:
        return SATELLITE_EXTENSIONS

    def supports(self, fpath: Path) -> bool:
        fname = fpath.name.lower()
        if fname in IGNORED_SATELLITES:
            return False
        if fname in SATELLITE_FILENAMES or fname.startswith("requirements"):
            return True
        return fpath.suffix.lower() in SATELLITE_EXTENSIONS

    def parse_file(self, file_path: Path, root_path: Path) -> FileParseResult | None:
        if not self.supports(file_path):
            return None
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        rel_path = file_path.resolve().relative_to(root_path.resolve()).as_posix()
        stem = file_path.stem.replace(".", "_")
        parent_parts = [p for p in Path(rel_path).parent.parts if p != "."]
        module_fqn = ".".join([*parent_parts, f"{stem}_sat"])

        lines = text.splitlines()
        total_lines = max(1, len(lines))
        ext = file_path.suffix.lower()
        fname = file_path.name.lower()

        entities: list[RawEntity] = []
        if ext == ".toml":
            entities = self._parse_toml(lines, module_fqn, total_lines)
        elif ext == ".json":
            entities = self._parse_json(lines, module_fqn, total_lines)
        elif ext in (".md", ".rst"):
            entities = self._parse_markdown(lines, module_fqn, total_lines)
        elif ext in (".yaml", ".yml"):
            entities = self._parse_yaml(lines, module_fqn, total_lines)
        elif fname == "makefile":
            entities = self._parse_makefile(lines, module_fqn, total_lines)
        elif fname in ("dockerfile", "containerfile"):
            entities = self._parse_dockerfile(lines, module_fqn)
        elif fname.startswith(("license", "requirements")):
            kind = NodeType.DOC.value if fname.startswith("license") else NodeType.CONFIG.value
            entities = [
                RawEntity(
                    name=file_path.name,
                    fqn=f"{module_fqn}.{file_path.stem}",
                    kind=kind,
                    line_start=1,
                    line_end=total_lines,
                    parent_fqn=module_fqn,
                )
            ]

        return FileParseResult(
            file_path=rel_path,
            module_fqn=module_fqn,
            entities=entities,
            language="satellite",
        )

    def _parse_toml(
        self, lines: list[str], module_fqn: str, total_lines: int
    ) -> list[RawEntity]:
        entities: list[RawEntity] = []
        curr_section: str | None = None
        curr_start = 1

        for idx, line in enumerate(lines, start=1):
            sec_match = _TOML_SECTION_RE.match(line)
            if sec_match:
                if curr_section is not None:
                    entities.append(
                        RawEntity(
                            name=curr_section,
                            fqn=f"{module_fqn}.{curr_section}",
                            kind=NodeType.CONFIG.value,
                            line_start=curr_start,
                            line_end=idx - 1,
                            parent_fqn=module_fqn,
                        )
                    )
                curr_section = sec_match.group(1).strip()
                curr_start = idx
                continue

            key_match = _TOML_KEY_RE.match(line)
            if key_match:
                key_name = key_match.group(1).strip()
                scope = f"{module_fqn}.{curr_section}" if curr_section else module_fqn
                entities.append(
                    RawEntity(
                        name=key_name,
                        fqn=f"{scope}.{key_name}",
                        kind=NodeType.CONFIG.value,
                        line_start=idx,
                        line_end=idx,
                        parent_fqn=module_fqn,
                    )
                )

        if curr_section is not None:
            entities.append(
                RawEntity(
                    name=curr_section,
                    fqn=f"{module_fqn}.{curr_section}",
                    kind=NodeType.CONFIG.value,
                    line_start=curr_start,
                    line_end=total_lines,
                    parent_fqn=module_fqn,
                )
            )
        return entities

    def _parse_json(
        self, lines: list[str], module_fqn: str, total_lines: int
    ) -> list[RawEntity]:
        anchors: list[tuple[str, int]] = []
        for idx, line in enumerate(lines, start=1):
            m = _JSON_KEY_RE.match(line)
            if m:
                anchors.append((m.group(2), idx))

        entities: list[RawEntity] = []
        for i, (key_name, start_line) in enumerate(anchors):
            end_line = anchors[i + 1][1] - 1 if i + 1 < len(anchors) else total_lines
            entities.append(
                RawEntity(
                    name=key_name,
                    fqn=f"{module_fqn}.{key_name}",
                    kind=NodeType.CONFIG.value,
                    line_start=start_line,
                    line_end=max(start_line, end_line),
                    parent_fqn=module_fqn,
                )
            )
        return entities

    def _parse_markdown(
        self, lines: list[str], module_fqn: str, total_lines: int
    ) -> list[RawEntity]:
        headings: list[tuple[str, int]] = []
        in_fence = False
        for idx, line in enumerate(lines, start=1):
            if line.strip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            m = _MD_HEADING_RE.match(line)
            if m:
                headings.append((m.group(2).strip(), idx))

        entities: list[RawEntity] = []
        for i, (title, start_line) in enumerate(headings):
            end_line = headings[i + 1][1] - 1 if i + 1 < len(headings) else total_lines
            safe_title = re.sub(r"\W+", "_", title).strip("_") or f"h_{start_line}"
            entities.append(
                RawEntity(
                    name=title,
                    fqn=f"{module_fqn}.{safe_title}",
                    kind=NodeType.DOC.value,
                    line_start=start_line,
                    line_end=max(start_line, end_line),
                    parent_fqn=module_fqn,
                )
            )
        return entities

    def _parse_yaml(
        self, lines: list[str], module_fqn: str, total_lines: int
    ) -> list[RawEntity]:
        keys: list[tuple[str, int]] = []
        for idx, line in enumerate(lines, start=1):
            m = _YAML_KEY_RE.match(line)
            if m:
                keys.append((m.group(1), idx))

        entities: list[RawEntity] = []
        for i, (key_name, start_line) in enumerate(keys):
            end_line = keys[i + 1][1] - 1 if i + 1 < len(keys) else total_lines
            entities.append(
                RawEntity(
                    name=key_name,
                    fqn=f"{module_fqn}.{key_name}",
                    kind=NodeType.CONFIG.value,
                    line_start=start_line,
                    line_end=max(start_line, end_line),
                    parent_fqn=module_fqn,
                )
            )
        return entities

    def _parse_makefile(
        self, lines: list[str], module_fqn: str, total_lines: int
    ) -> list[RawEntity]:
        targets: list[tuple[str, int]] = []
        for idx, line in enumerate(lines, start=1):
            m = _MAKE_TARGET_RE.match(line)
            if m and not m.group(1).startswith("."):
                targets.append((m.group(1), idx))

        entities: list[RawEntity] = []
        for i, (target, start_line) in enumerate(targets):
            end_line = targets[i + 1][1] - 1 if i + 1 < len(targets) else total_lines
            entities.append(
                RawEntity(
                    name=target,
                    fqn=f"{module_fqn}.{target}",
                    kind=NodeType.CONFIG.value,
                    line_start=start_line,
                    line_end=max(start_line, end_line),
                    parent_fqn=module_fqn,
                )
            )
        return entities

    def _parse_dockerfile(self, lines: list[str], module_fqn: str) -> list[RawEntity]:
        entities: list[RawEntity] = []
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.upper().startswith(("FROM ", "ENTRYPOINT ", "CMD ", "EXPOSE ")):
                cmd = stripped.split()[0].upper()
                entities.append(
                    RawEntity(
                        name=stripped[:40],
                        fqn=f"{module_fqn}.{cmd}_{idx}",
                        kind=NodeType.CONFIG.value,
                        line_start=idx,
                        line_end=idx,
                        parent_fqn=module_fqn,
                    )
                )
        return entities
