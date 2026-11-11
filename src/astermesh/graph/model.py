from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# graph node classifications
class NodeType(str, Enum):
    FOLDER = "folder"
    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"
    VARIABLE = "variable"
    COMPONENT = "component"
    HOOK = "hook"
    SERVICE = "service"
    MODULE = "module"
    CONFIG = "config"
    DOC = "doc"
    ASSET = "asset"
    COMMUNITY = "community"


# graph edge classifications
class EdgeType(str, Enum):
    CONTAINS = "CONTAINS"
    DEFINES = "DEFINES"
    CALLS = "CALLS"
    INHERITS = "INHERITS"
    IMPORTS = "IMPORTS"
    REFERENCES = "REFERENCES"
    RENDERS = "RENDERS"
    INJECTS = "INJECTS"
    PROVIDES = "PROVIDES"


EDGE_WEIGHTS: dict[str, float] = {
    EdgeType.DEFINES.value: 3.0,
    EdgeType.INHERITS.value: 2.5,
    EdgeType.INJECTS.value: 2.5,
    EdgeType.RENDERS.value: 2.0,
    EdgeType.CALLS.value: 1.5,
    EdgeType.PROVIDES.value: 1.0,
    EdgeType.IMPORTS.value: 1.0,
    EdgeType.REFERENCES.value: 0.8,
    EdgeType.CONTAINS.value: 1.0,
}

LOGIC_NODE_TYPES: frozenset[str] = frozenset({
    NodeType.CLASS.value,
    NodeType.FUNCTION.value,
    NodeType.VARIABLE.value,
    NodeType.COMPONENT.value,
    NodeType.HOOK.value,
    NodeType.SERVICE.value,
    NodeType.MODULE.value,
})


# graph entity node schema
@dataclass
class EntityNode:
    id: str
    name: str
    node_type: NodeType
    file_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    parameters: list[str] = field(default_factory=list)
    is_async: bool = False
    is_method: bool = False
    is_exported: bool = False
    community_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.node_type.value,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "parameters": self.parameters,
            "is_async": self.is_async,
            "is_method": self.is_method,
            "is_exported": self.is_exported,
            "community_id": self.community_id,
        }


# community cluster metadata
@dataclass
class CommunityInfo:
    id: str
    name: str
    god_node: str
    size: int
    members: list[str] = field(default_factory=list)
