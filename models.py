from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime


class NodeUpdate(BaseModel):
    original_name: str
    name: Optional[str] = None
    sw_id: Optional[int] = None
    parent_id: Optional[int] = None
    node_type: Optional[str] = None
    link_type: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    serial_no: Optional[str] = None
    mac: Optional[str] = None
    ip: Optional[str] = None
    split_ratio: Optional[int] = None
    split_group: Optional[str] = None
    cable_id: Optional[str] = None
    cable_start: Optional[int] = None
    cable_end: Optional[int] = None
    cable_length: Optional[int] = None
    cable_color: Optional[str] = None
    cable_desc: Optional[str] = None
    lat1: Optional[float] = None
    long1: Optional[float] = None
    vlan: Optional[str] = None
    location: Optional[str] = None
    remarks: Optional[str] = None
    position_x: Optional[float] = None
    position_y: Optional[float] = None
    position_mode: Optional[int] = None


class OnuCustomerInfo(BaseModel):
    port: Optional[str] = None
    portno: Optional[int] = None
    cid: Optional[int] = None
    uname: Optional[str] = None
    expiry_date: Optional[datetime] = None
    mac: Optional[str] = None
    owner: Optional[str] = None
    status: Optional[int] = None
    ls: Optional[int] = None
    cls: Optional[int] = None
    online1: Optional[int] = None
    st2: Optional[str] = None
    diff: Optional[float] = None


class NodeCopy(BaseModel):
    source_node_id: int
    new_parent_id: int


class PositionReset(BaseModel):
    sw_id: Optional[int] = None
    scope: Optional[str] = None
    node_id: Optional[int] = None


class EdgeDeleteByName(BaseModel):
    name: str
    source_id: int
    sw_id: Optional[int] = None


class NodeDeleteByName(BaseModel):
    name: str
    sw_id: Optional[int] = None


class NodeCreate(BaseModel):
    name: str
    sw_id: Optional[int] = None
    parent_id: Optional[int] = None
    node_type: Optional[str] = None
    link_type: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    serial_no: Optional[str] = None
    mac: Optional[str] = None
    ip: Optional[str] = None
    split_ratio: Optional[int] = None
    split_group: Optional[str] = None
    cable_id: Optional[str] = None
    cable_start: Optional[int] = None
    cable_end: Optional[int] = None
    cable_length: Optional[int] = None
    cable_color: Optional[str] = None
    cable_desc: Optional[str] = None
    lat1: Optional[float] = None
    long1: Optional[float] = None
    vlan: Optional[str] = None
    location: Optional[str] = None
    remarks: Optional[str] = None


class NodeInsert(BaseModel):
    new_node_data: Dict[str, Any]
    original_source_id: int
    original_edge_record_id: int
