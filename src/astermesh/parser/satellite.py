import re
import xml.etree.ElementTree as ET
from pathlib import Path

from astermesh.graph.model import NodeType
from astermesh.parser.types import FileParseResult, RawEntity

SATELLITE_EXTENSIONS: frozenset[str] = frozenset({
    ".toml",
    ".json",
    ".yaml",
    ".yml",
    ".properties",
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
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
})

IGNORED_SATELLITES: frozenset[str] = frozenset({
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "cargo.lock",
    "uv.lock",
    "poetry.lock",
})

MAX_SATELLITE_ENTITIES = 40
_MAX_NON_MANIFEST_BYTES = 64 * 1024
_BLOAT_PATH_TOKENS = ("sample-data", "seed-data", "mock-data", "test-data")
_RESOURCE_DATA_DIR_RE = re.compile(
    r"(?:^|/)(?:spdx|licen[cs]es?)/|"
    r"(?:^|/)src/main/resources/(?:[^/]+/)*(?:i18n|locales?|fixtures?)/",
    re.IGNORECASE,
)
_CAPPED_CONFIG_EXTS = frozenset({".json", ".yaml", ".yml", ".properties"})
_MANIFEST_FILENAMES = frozenset({
    "package.json",
    "tsconfig.json",
    "jsconfig.json",
    "composer.json",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    "application.properties",
})

_TOML_SECTION_RE = re.compile(r"^\s*\[+([^\[\]]+)\]+\s*$")
_TOML_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*=")
_JSON_KEY_RE = re.compile(r'^(\s{1,4}|\t)"([^"]+)"\s*:')
_MD_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
_YAML_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*:")
_MAKE_TARGET_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*:")
_PROP_KEY_RE = re.compile(r"^\s*([A-Za-z0-9_.\-\[\]/]+)\s*[=:]")
_GRADLE_PLUGIN_ID_RE = re.compile(r"""\bid\s*\(?\s*['"]([^'"]+)['"]\s*\)?""")
_GRADLE_KOTLIN_PLUGIN_RE = re.compile(r"""\bkotlin\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_GRADLE_APPLY_PLUGIN_RE = re.compile(
    r"""\bapply\s*\(?\s*plugin\s*[:=]\s*['"]([^'"]+)['"]"""
)
_GRADLE_DEP_COORD_RE = re.compile(
    r"""['"]([A-Za-z0-9_.-]+):([A-Za-z0-9_.-]+)(?::[^'"]*)?['"]"""
)
_GRADLE_DEP_MAP_RE = re.compile(
    r"""\b(?:implementation|api|compileOnly|runtimeOnly|annotationProcessor|"""
    r"""testImplementation|testCompileOnly|testRuntimeOnly|compile|testCompile|"""
    r"""classpath)\b.*\bname\s*[:=]\s*['"]([^'"]+)['"]"""
)
_GRADLE_PROJECT_RE = re.compile(r"""\bproject\s*\(\s*['"]:?([^'"]+)['"]\s*\)""")
_GRADLE_INCLUDE_RE = re.compile(r"^\s*include(?:Flat)?\b(.*)$")
_QUOTED_STR_RE = re.compile(r"""['"]([^'"]+)['"]""")


def _xml_local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[1] if "}" in tag else tag


def _find_xml_line(lines: list[str], value: str, tag: str | None = None) -> int:
    tag_hint = f"<{tag}" if tag else None
    for idx, line in enumerate(lines, start=1):
        if value in line and (tag_hint is None or tag_hint in line):
            return idx
    for idx, line in enumerate(lines, start=1):
        if value in line:
            return idx
    return 1


def _has_trailing_escape(line: str) -> bool:
    stripped = line.rstrip("\r\n")
    bs_count = len(stripped) - len(stripped.rstrip("\\"))
    return bs_count % 2 == 1


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

    def _is_manifest_file(self, fname_lower: str) -> bool:
        if fname_lower in _MANIFEST_FILENAMES:
            return True
        return fname_lower.startswith("application") and fname_lower.endswith((".yml", ".yaml"))

    def parse_file(self, file_path: Path, root_path: Path) -> FileParseResult | None:
        if not self.supports(file_path):
            return None

        rel_path = file_path.resolve().relative_to(root_path.resolve()).as_posix()
        ext = file_path.suffix.lower()
        fname = file_path.name.lower()
        is_capped_ext = ext in _CAPPED_CONFIG_EXTS
        is_manifest = self._is_manifest_file(fname)

        if is_capped_ext and not is_manifest:
            rel_lower = rel_path.lower()
            if any(tok in rel_lower for tok in _BLOAT_PATH_TOKENS):
                return None
            if _RESOURCE_DATA_DIR_RE.search(rel_lower):
                return None
            try:
                if file_path.stat().st_size > _MAX_NON_MANIFEST_BYTES:
                    return None
            except OSError:
                return None

        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        stem = file_path.stem.replace(".", "_")
        parent_parts = [p for p in Path(rel_path).parent.parts if p != "."]
        module_fqn = ".".join([*parent_parts, f"{stem}_sat"])

        lines = text.splitlines()
        total_lines = max(1, len(lines))

        entities: list[RawEntity] = []
        if ext == ".toml":
            entities = self._parse_toml(lines, module_fqn, total_lines)
        elif ext == ".json":
            entities = self._parse_json(lines, module_fqn, total_lines)
        elif ext in (".md", ".rst"):
            entities = self._parse_markdown(lines, module_fqn, total_lines)
        elif ext in (".yaml", ".yml"):
            entities = self._parse_yaml(lines, module_fqn, total_lines)
        elif ext == ".properties":
            entities = self._parse_properties(lines, module_fqn)
        elif fname == "pom.xml":
            entities = self._parse_pom_xml(text, lines, module_fqn)
        elif fname in (
            "build.gradle",
            "build.gradle.kts",
            "settings.gradle",
            "settings.gradle.kts",
        ):
            entities = self._parse_gradle(lines, module_fqn)
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

        if is_capped_ext and not is_manifest and len(entities) > MAX_SATELLITE_ENTITIES:
            entities = entities[:MAX_SATELLITE_ENTITIES]

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

    def _append_unique_entity(
        self,
        entities: list[RawEntity],
        seen_fqns: set[str],
        name: str,
        base_fqn: str,
        line_num: int,
        module_fqn: str,
    ) -> None:
        fqn = base_fqn if base_fqn not in seen_fqns else f"{base_fqn}_{line_num}"
        if fqn in seen_fqns:
            fqn = f"{base_fqn}_{len(entities)}"
        seen_fqns.add(fqn)
        entities.append(
            RawEntity(
                name=name,
                fqn=fqn,
                kind=NodeType.CONFIG.value,
                line_start=line_num,
                line_end=line_num,
                parent_fqn=module_fqn,
            )
        )

    def _parse_pom_xml(
        self, text: str, lines: list[str], module_fqn: str
    ) -> list[RawEntity]:
        upper = text.upper()
        if not text.strip() or "<!ENTITY" in upper or "<!DOCTYPE" in upper:
            return []
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return []

        entities: list[RawEntity] = []
        seen_fqns: set[str] = set()
        direct_gid: str | None = None
        parent_gid: str | None = None
        direct_aid: str | None = None

        for child in root:
            ctag = _xml_local_tag(child.tag)
            if ctag == "groupId" and child.text and child.text.strip():
                direct_gid = child.text.strip()
            elif ctag == "artifactId" and child.text and child.text.strip():
                direct_aid = child.text.strip()
            elif ctag == "parent":
                for pchild in child:
                    if _xml_local_tag(pchild.tag) == "groupId" and pchild.text:
                        val = pchild.text.strip()
                        if val:
                            parent_gid = val

        gid = direct_gid or parent_gid
        if gid:
            ln = _find_xml_line(lines, gid, "groupId")
            self._append_unique_entity(
                entities, seen_fqns, gid, f"{module_fqn}.groupId.{gid}", ln, module_fqn
            )
        if direct_aid:
            ln = _find_xml_line(lines, direct_aid, "artifactId")
            self._append_unique_entity(
                entities,
                seen_fqns,
                direct_aid,
                f"{module_fqn}.artifactId.{direct_aid}",
                ln,
                module_fqn,
            )

        for elem in root.iter():
            etag = _xml_local_tag(elem.tag)
            if etag == "module" and elem.text and elem.text.strip():
                mod_name = elem.text.strip()
                ln = _find_xml_line(lines, mod_name, "module")
                self._append_unique_entity(
                    entities,
                    seen_fqns,
                    mod_name,
                    f"{module_fqn}.module.{mod_name}",
                    ln,
                    module_fqn,
                )
            elif etag == "dependency":
                for dchild in elem:
                    if _xml_local_tag(dchild.tag) == "artifactId" and dchild.text:
                        dep_aid = dchild.text.strip()
                        if dep_aid:
                            ln = _find_xml_line(lines, dep_aid, "artifactId")
                            self._append_unique_entity(
                                entities,
                                seen_fqns,
                                dep_aid,
                                f"{module_fqn}.dependency.{dep_aid}",
                                ln,
                                module_fqn,
                            )
                        break

        return entities

    def _parse_gradle(self, lines: list[str], module_fqn: str) -> list[RawEntity]:
        entities: list[RawEntity] = []
        seen_fqns: set[str] = set()
        in_block_comment = False
        in_include_block = False

        for idx, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line:
                continue
            if in_block_comment:
                if "*/" in line:
                    in_block_comment = False
                continue
            if line.startswith("/*"):
                if "*/" not in line[2:]:
                    in_block_comment = True
                continue
            if line.startswith("//"):
                continue

            if in_include_block:
                for mod in _QUOTED_STR_RE.findall(line):
                    clean = mod.lstrip(":") or mod
                    self._append_unique_entity(
                        entities, seen_fqns, clean, f"{module_fqn}.include.{clean}", idx, module_fqn
                    )
                if ")" in line:
                    in_include_block = False
                continue

            inc_m = _GRADLE_INCLUDE_RE.match(line)
            if inc_m:
                rest = inc_m.group(1)
                for mod in _QUOTED_STR_RE.findall(rest):
                    clean = mod.lstrip(":") or mod
                    self._append_unique_entity(
                        entities, seen_fqns, clean, f"{module_fqn}.include.{clean}", idx, module_fqn
                    )
                if "(" in rest and ")" not in rest:
                    in_include_block = True
                continue

            for m in _GRADLE_PLUGIN_ID_RE.finditer(line):
                pid = m.group(1)
                self._append_unique_entity(
                    entities, seen_fqns, pid, f"{module_fqn}.plugin.{pid}", idx, module_fqn
                )
            for m in _GRADLE_KOTLIN_PLUGIN_RE.finditer(line):
                kpid = m.group(1)
                self._append_unique_entity(
                    entities, seen_fqns, kpid, f"{module_fqn}.plugin.{kpid}", idx, module_fqn
                )
            for m in _GRADLE_APPLY_PLUGIN_RE.finditer(line):
                apid = m.group(1)
                self._append_unique_entity(
                    entities, seen_fqns, apid, f"{module_fqn}.plugin.{apid}", idx, module_fqn
                )

            coord_matched = False
            for m in _GRADLE_DEP_COORD_RE.finditer(line):
                coord_matched = True
                artifact = m.group(2)
                self._append_unique_entity(
                    entities, seen_fqns, artifact, f"{module_fqn}.dep.{artifact}", idx, module_fqn
                )
            if not coord_matched:
                map_m = _GRADLE_DEP_MAP_RE.search(line)
                if map_m:
                    artifact = map_m.group(1)
                    self._append_unique_entity(
                        entities,
                        seen_fqns,
                        artifact,
                        f"{module_fqn}.dep.{artifact}",
                        idx,
                        module_fqn,
                    )

            for m in _GRADLE_PROJECT_RE.finditer(line):
                proj = m.group(1)
                self._append_unique_entity(
                    entities, seen_fqns, proj, f"{module_fqn}.project.{proj}", idx, module_fqn
                )

        return entities

    def _parse_properties(self, lines: list[str], module_fqn: str) -> list[RawEntity]:
        entities: list[RawEntity] = []
        seen_fqns: set[str] = set()
        in_continuation = False

        for idx, line in enumerate(lines, start=1):
            if in_continuation:
                in_continuation = _has_trailing_escape(line)
                continue
            stripped = line.lstrip()
            if not stripped or stripped.startswith(("#", "!")):
                continue
            m = _PROP_KEY_RE.match(line)
            if m:
                key = m.group(1)
                self._append_unique_entity(
                    entities, seen_fqns, key, f"{module_fqn}.{key}", idx, module_fqn
                )
            in_continuation = _has_trailing_escape(line)

        return entities
