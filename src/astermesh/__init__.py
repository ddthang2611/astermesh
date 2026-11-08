# public api facade
from astermesh.graph.builder import GraphBuilder
from astermesh.graph.model import CommunityInfo, EdgeType, EntityNode, NodeType
from astermesh.graph.query import GraphQuery
from astermesh.parser.engine import ASTEngine

__version__ = "1.1.0"

__all__ = [
    "GraphBuilder",
    "ASTEngine",
    "GraphQuery",
    "NodeType",
    "EdgeType",
    "EntityNode",
    "CommunityInfo",
    "create_mcp_server",
]



def __getattr__(name: str):
    if name == "create_mcp_server":
        from astermesh.server import create_mcp_server
        return create_mcp_server
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
