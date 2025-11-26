from pydantic import BaseModel
from typing import Optional, Dict, Any, List, Union
from datetime import datetime


class EdgeCreate(BaseModel):
    source_id: int
    target_id: int
    link_type: Optional[str] = "Fiber Optic"
    cable_color: Optional[str] = "#1e293b"


class DeviceSearchItem(BaseModel):
    id: int
    name: str
    node_type: str

    class Config:
        from_attributes = True


class DeviceBase(BaseModel):
    """Base fields for a device from ftth_devices."""

    name: Optional[str] = None
    node_type: Optional[str] = None
    sw_id: Optional[int] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    serial_no: Optional[str] = None
    mac: Optional[str] = None
    ip: Optional[str] = None
    split_color: Optional[str] = None
    split_ratio: Optional[int] = None
    split_group: Optional[str] = None
    lat1: Optional[float] = None
    long1: Optional[float] = None
    vlan: Optional[str] = None
    location: Optional[str] = None
    remarks: Optional[str] = None
    position_x: Optional[float] = None
    position_y: Optional[float] = None
    position_mode: Optional[int] = None
    status: Optional[int] = None
    pop_id: Optional[int] = None
    container_id: Optional[int] = None
    area_id: Optional[int] = None
    device_type: Optional[str] = None


class EdgeBase(BaseModel):
    """Base fields for an edge from ftth_edges."""

    link_type: Optional[str] = None
    cable_id: Optional[str] = None
    cable_start: Optional[int] = None
    cable_end: Optional[int] = None
    cable_length: Optional[int] = None
    cable_color: Optional[str] = None
    cable_desc: Optional[str] = None


class DeviceData(DeviceBase):
    """Device data returned from the API."""

    id: int
    name: str


class EdgeData(EdgeBase):
    """Edge data returned from the API."""

    id: int
    source_id: int
    target_id: int


class NodeDetailsResponse(BaseModel):
    """
    The complete response for the Edit Node Modal.
    Contains the device and ALL its connected edges.
    """

    device: DeviceData
    incoming_edges: List[EdgeData]
    outgoing_edges: List[EdgeData]


class TracePathRequest(BaseModel):
    source_id: int
    target_id: int
    include_others: bool = False  # False = Direct, True = Neighboring/Children


class TracePathResponse(BaseModel):
    devices: List[DeviceData]
    edges: List[EdgeData]


class CustomerIndexItem(BaseModel):
    cid: str
    mac: str
    onu_id: int
    onu_name: str


# --- Models for PUT /node-details/{node_id} ---


class DeviceUpdate(DeviceBase):
    """Payload for updating just the device fields."""

    # All fields are optional, inheriting from DeviceBase
    pass


class EdgeUpdate(EdgeBase):
    """Payload for updating a single edge."""

    id: int  # Required to know *which* edge to update in the DB


class NodeDetailsUpdate(BaseModel):
    """
    The new, complete payload to save all changes from the edit modal.
    This REPLACES the old flat 'NodeUpdate' model.
    """

    device_data: DeviceUpdate
    edges_to_update: List[EdgeUpdate]  # A list of all edges that were modified


# =======================================================
# === Other Models (Unchanged) ==========================
# =======================================================


class OnuCustomerInfo(BaseModel):
    port: Optional[str] = None
    portno: Optional[int] = None
    cid: Optional[Union[str, int]] = None  # Allow both str and int
    uname: Optional[Union[str, int]] = None  # Allow both str and int
    expiry_date: Optional[datetime] = None
    mac: Optional[str] = None
    owner: Optional[str] = None
    status: Optional[int] = None
    ls: Optional[int] = None
    cls: Optional[int] = None
    online1: Optional[int] = None
    st2: Optional[str] = None
    diff: Optional[float] = None


class PositionReset(BaseModel):
    sw_id: Optional[int] = None
    scope: Optional[str] = None
    node_id: Optional[int] = None


class NodeInsert(BaseModel):
    new_node_data: Dict[str, Any]
    original_source_id: int
    original_edge_record_id: int
