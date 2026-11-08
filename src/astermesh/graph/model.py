from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# phan loai dinh va canh
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


# cau truc thuc the do thi
@dataclass
class EntityNode:
    id: str
    name: str
    node_type: NodeType
    file_path: Optional[str] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    parameters: List[str] = field(default_factory=list)
    is_async: bool = False
    is_method: bool = False
    community_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
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


# thong tin cum cong dong
@dataclass
class CommunityInfo:
    id: str
    name: str
    god_node: str
    size: int
    members: List[str] = field(default_factory=list)
