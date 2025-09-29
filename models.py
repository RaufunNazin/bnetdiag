from pydantic import BaseModel
from typing import Optional


class NodeUpdate(BaseModel):
    name: Optional[str] = None
    node_type: Optional[str] = None
    link_type: Optional[str] = None
    parent_id: Optional[int] = None
    status: Optional[int] = None
    sw_id: Optional[int] = None
    vlan: Optional[int] = None
    cable_color: Optional[str] = None
    serial_no: Optional[str] = None
    brand: Optional[str] = None
    mac: Optional[str] = None
    model: Optional[str] = None
    remarks: Optional[str] = None


class NodeCopy(BaseModel):
    source_node_id: int
    new_parent_id: int


class EdgeDeleteByName(BaseModel):
    name: str  # The 'name' of the child node (e.g., "EPON0/5:1")
    source_id: int  # The 'id' of the parent node (e.g., the PON's ID)
    sw_id: int  # The 'id' of the OLT


class NodeDeleteByName(BaseModel):
    name: str
    sw_id: int
