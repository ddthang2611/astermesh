from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# graph node and edge classifications
class NodeType(str, Enum):
    FOLDER = "folder"
    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"
    VARIABLE = "variable"
    COMMUNITY = "community"


class EdgeType(str, Enum):
    CONTAINS = "CONTAINS"
    DEFINES = "DEFINES"
    CALLS = "CALLS"
    INHERITS = "INHERITS"
    IMPORTS = "IMPORTS"
    REFERENCES = "REFERENCES"


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
