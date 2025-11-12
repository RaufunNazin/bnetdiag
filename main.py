# main.py
from typing import Any, Dict, List
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from datetime import timedelta
import oracledb
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from database import get_connection
from models import (
    NodeUpdate,
    NodeCopy,
    EdgeDeleteByName,
    NodeDeleteByName,
    NodeCreate,
    NodeInsert,
    PositionReset,
    OnuCustomerInfo,
)
from auth import (
    Token,
    User,
    get_user_password_from_db,
    pwd_context,
    get_user_from_db,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    get_current_user,
)

app = FastAPI(title="netdiag-backend", version="1.0.0")

allowed_hosts = ["http://localhost:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_hosts,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)


def _check_node_ownership(node_id: int, current_user: User, cursor: oracledb.Cursor):
    """
    SECURITY HELPER: Checks if a user has permission to access a specific device
    by matching their area_id.
    --- UPDATED ---
    """
    if current_user.role_id in [2, 3]:
        if current_user.area_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your account is not assigned to an area.",
            )

        # --- UPDATED ---
        sql = "SELECT area_id FROM ftth_devices WHERE id = :node_id"
        cursor.execute(sql, {"node_id": node_id})
        row = cursor.fetchone()

        if not row or row[0] != current_user.area_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied: You do not have access to this component.",
            )
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You are not authorized to view this data.",
    )


@app.get("/")
def read_root():
    return {"message": "FastAPI is running. Visit /token to login."}


@app.get("/test-oracle")
def test_oracle_connection():
    conn = None
    try:
        conn = get_connection()
        print("✅ Connection successful!")
        cursor = conn.cursor()
        sql = "SELECT user, sysdate FROM dual"
        print(f"Executing query: {sql}")
        cursor.execute(sql)
        result = cursor.fetchone()
        cursor.close()
        if result:
            db_user, db_date = result
            return {
                "status": "success",
                "database_user": db_user,
                "database_time": db_date.strftime("%Y-%m-%d %H:%M:%S"),
            }
        else:
            raise HTTPException(status_code=404, detail="Query returned no results.")
    except oracledb.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"An unexpected error occurred: {e}"
        )
    finally:
        if conn:
            conn.close()
            print("Connection closed.")


@app.post("/token", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    hashed_password = get_user_password_from_db(form_data.username)
    if not hashed_password or not pwd_context.verify(
        form_data.password, hashed_password
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = get_user_from_db(form_data.username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not find user after login.",
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={
            "sub": user.username,
            "user_id": user.id,
            "role_id": user.role_id,
            "area_id": user.area_id,
            "first_name": user.first_name,
        },
        expires_delta=access_token_expires,
    )
    return {"access_token": access_token, "token_type": "bearer"}


@app.get(
    "/onu/{olt_id}/{port_name:path}/customers", response_model=List[OnuCustomerInfo]
)
def get_onu_customer_details(
    olt_id: int, port_name: str, current_user: User = Depends(get_current_user)
):
    """
    Fetches customer details for a specific ONU port on a given OLT.
    --- UPDATED ---
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        if current_user.role_id in [2, 3]:
            if current_user.area_id is None:
                raise HTTPException(
                    status_code=403, detail="Your account is not assigned to an area."
                )

            # --- UPDATED ---
            cursor.execute(
                "SELECT area_id FROM ftth_devices WHERE id = :olt_id_bv",
                {"olt_id_bv": olt_id},
            )
            row = cursor.fetchone()
            if not row or row[0] != current_user.area_id:
                raise HTTPException(
                    status_code=403,
                    detail="Permission denied: You do not own this OLT.",
                )
        else:
            raise HTTPException(status_code=403, detail="Not authorized.")

        # --- UPDATED ---
        sql = """
            SELECT port, get_customer_id (h.user_id) cid, get_username (h.user_id) uname,
                   expiry_date, m.mac, get_full_name (owner_id) owner, h.status, ls,
                   nvl(class_id,-1) cls, is_online3 (h.user_id) online1, GET_USER_STATUS(h.user_id) st2,
                   sysdate-m.udate diff
            FROM OLT_CUSTOMER_MAC_2 m, ftth_devices p, home_conn h 
            WHERE h.user_id=m.user_id
              AND p.name=m.port
              AND m.olt_id=p.sw_id
              AND m.olt_id = :olt_id_bv
              AND m.port = :port_name_bv
            ORDER BY m.port
        """

        params = {"olt_id_bv": olt_id, "port_name_bv": port_name}
        cursor.execute(sql, params)

        columns = [desc[0].lower() for desc in cursor.description]
        rows = cursor.fetchall()
        results = [dict(zip(columns, row)) for row in rows]
        return results

    except oracledb.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        if conn:
            conn.close()


@app.post("/positions/reset", status_code=200)
def reset_node_positions(
    reset_request: PositionReset, current_user: User = Depends(get_current_user)
):
    """
    Resets device positions based on the provided scope.
    --- UPDATED ---
    """
    # --- UPDATED ---
    base_sql = """
        UPDATE ftth_devices
        SET position_x = NULL,
            position_y = NULL,
            position_mode = 0
    """
    where_clauses = []
    params = {}

    if current_user.role_id not in [2, 3]:
        raise HTTPException(status_code=403, detail="Not authorized.")
    if current_user.area_id is None:
        raise HTTPException(
            status_code=403, detail="Your account is not assigned to an area."
        )

    where_clauses.append("area_id = :area_id")
    params["area_id"] = current_user.area_id

    if reset_request.node_id:
        where_clauses.append("id = :node_id")
        params["node_id"] = reset_request.node_id
    elif reset_request.scope:
        if reset_request.sw_id is not None:
            where_clauses.append("sw_id = :sw_id")
            params["sw_id"] = reset_request.sw_id
        else:
            # --- UPDATED ---
            general_view_clause = """
            (node_type NOT IN ('PON', 'ONU') AND NOT EXISTS (
                SELECT 1 FROM ftth_edges e WHERE e.target_id = id
            ) OR id IN (
                SELECT d.id FROM ftth_devices d
                LEFT JOIN ftth_edges e ON d.id = e.target_id
                WHERE d.node_type NOT IN ('PON', 'ONU')
                AND (e.source_id IS NULL OR e.source_id NOT IN (
                    SELECT id FROM ftth_devices 
                    WHERE node_type IN ('OLT', 'PON', 'ONU') AND sw_id IS NOT NULL
                ))
            ))
            AND sw_id IS NULL
            """
            where_clauses.append(general_view_clause)

        if reset_request.scope == "manual":
            where_clauses.append("position_mode = 1")
        elif reset_request.scope != "all":
            raise HTTPException(
                status_code=400, detail="Invalid scope. Must be 'all' or 'manual'."
            )
    else:
        raise HTTPException(
            status_code=400,
            detail="Either node_id or a scope (with or without sw_id) must be provided.",
        )

    final_sql = f"{base_sql} WHERE {' AND '.join(where_clauses)}"

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(final_sql, params)
        conn.commit()

        if cursor.rowcount == 0:
            return {"message": "No devices matched the criteria for reset."}

        return {"message": f"{cursor.rowcount} device positions were reset."}
    except oracledb.Error as e:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        if conn:
            conn.close()


@app.get("/data", response_model=List[Dict[str, Any]])
async def read_general_data(current_user: User = Depends(get_current_user)):
    """
    Endpoint for the general network view.
    Calls read_data without a root_node_id.
    """
    return read_data(root_node_id=None, current_user=current_user)


@app.get("/data/{root_node_id}")
def read_data(root_node_id: int, current_user: User = Depends(get_current_user)):
    """
    Endpoint to get a specific node and all its descendants.
    --- UPDATED ---
    Re-joins devices and edges to simulate the old 'nodes' table structure.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        sql = ""
        params = {}
        auth_clause = ""
        auth_clause_connect_by = ""

        if current_user.role_id not in [2, 3]:
            return []
        if current_user.area_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your account is not assigned to an area.",
            )

        # --- UPDATED ---
        auth_clause = " AND d.area_id = :user_area_id"
        auth_clause_connect_by = " AND PRIOR d.area_id = :user_area_id"
        params["user_area_id"] = current_user.area_id

        # --- UPDATED ---
        # Define the selection columns to join device and edge data
        selection_cols = """
          d.ID, d.NAME, d.NODE_TYPE, d.STATUS, d.SW_ID, d.POP_ID, d.VLAN,
          d.SPLIT_RATIO, d.SPLIT_COLOR_GRP, d.SPLIT_COLOR, d.COLOR_GROUP,
          d.CONTAINER_ID, d.AREA_ID, d.REMARKS, d.USER_ID, d.SERIAL_NO,
          d.BRAND, d.LAT1, d.LONG1, d.IP, d.MAC, d.DEVICE_TYPE, d.MODEL,
          d.SPLIT_GROUP, d.POSITION_X, d.POSITION_Y, d.POSITION_MODE,
          e.SOURCE_ID as PARENT_ID,
          e.ID as EDGE_ID,
          e.LINK_TYPE, e.CABLE_ID, e.CABLE_LENGTH, e.CABLE_COLOR,
          e.CABLE_START, e.CABLE_DESC, e.CABLE_END, e.PARENT_PORT, e.SW_PORT2
        """

        if root_node_id is not None:
            _check_node_ownership(root_node_id, current_user, cursor)
            params["root_node_id_bv"] = root_node_id

            # Auth clauses for subqueries
            auth_clause_sub = auth_clause.replace(" d.", " d2.")
            auth_clause_connect_by_sub = auth_clause_connect_by.replace(" d.", " d2.")

            # --- UPDATED ---
            # This query joins devices and edges and uses the edge (parent)
            # to build the hierarchy.
            sql = f"""
                SELECT {selection_cols}
                FROM ftth_devices d
                LEFT JOIN ftth_edges e ON d.id = e.target_id
                WHERE 1=1 {auth_clause}
                START WITH d.id = :root_node_id_bv
                CONNECT BY PRIOR d.id = e.source_id {auth_clause_connect_by}
                
                UNION
                
                SELECT {selection_cols}
                FROM ftth_devices d
                LEFT JOIN ftth_edges e ON d.id = e.target_id
                WHERE d.id IN (
                    SELECT d2.id
                    FROM ftth_devices d2
                    LEFT JOIN ftth_edges e2 ON d2.id = e2.target_id
                    START WITH e2.source_id IS NULL AND d2.sw_id = :root_node_id_bv {auth_clause_sub}
                    CONNECT BY PRIOR d2.id = e2.source_id {auth_clause_connect_by_sub}
                ) {auth_clause}
            """
        else:
            # --- UPDATED ---
            # General view query, also joining devices and edges
            sql = f"""
                SELECT {selection_cols}
                FROM ftth_devices d
                LEFT JOIN ftth_edges e ON d.id = e.target_id
                WHERE (d.node_type NOT IN ('PON', 'ONU') AND (e.source_id IS NULL OR e.source_id NOT IN (
                    SELECT id FROM ftth_devices WHERE node_type IN ('OLT', 'PON', 'ONU') AND sw_id IS NOT NULL
                )) AND d.sw_id IS NULL)
                {auth_clause}
            """

        cursor.execute(sql, params)
        columns = [desc[0].lower() for desc in cursor.description]
        rows = cursor.fetchall()
        cursor.close()

        return [dict(zip(columns, row)) for row in rows] if rows else []

    except oracledb.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        if conn:
            conn.close()


@app.get("/nodes/root-candidates", response_model=List[Dict[str, Any]])
def get_root_candidates(current_user: User = Depends(get_current_user)):
    """
    Returns a list of devices that can be used as a root.
    --- UPDATED ---
    """
    if current_user.role_id not in [2, 3]:
        raise HTTPException(status_code=403, detail="Not authorized.")
    if current_user.area_id is None:
        raise HTTPException(
            status_code=403, detail="Your account is not assigned to an area."
        )
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # --- UPDATED ---
        sql = """
            SELECT id, name FROM ftth_devices 
            WHERE node_type IN ('Router', 'Managed Switch', 'Unmanaged Switch')
        """
        params = {}
        sql += " AND area_id = :area_id ORDER BY name"
        params["area_id"] = current_user.area_id

        cursor.execute(sql, params)
        columns = [desc[0].lower() for desc in cursor.description]
        rows = cursor.fetchall()
        cursor.close()
        return [dict(zip(columns, row)) for row in rows]
    except oracledb.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        if conn:
            conn.close()


@app.post("/node/insert", status_code=201)
def insert_node(
    insert_data: NodeInsert, current_user: User = Depends(get_current_user)
):
    """
    Inserts a new device between two existing devices.
    --- UPDATED ---
    """
    # --- UPDATED ---
    plsql_block = """
        DECLARE
            v_new_device_id       ftth_devices.id%TYPE;
            v_parent_area_id    ftth_devices.area_id%TYPE;
        BEGIN
            -- Step 1: Check permissions on original parent device
            SELECT area_id INTO v_parent_area_id
            FROM ftth_devices
            WHERE id = :original_source_id;

            -- Step 2: Enforce authorization
            IF (v_parent_area_id = :area_id) THEN

                -- Step 3: Insert the new device
                INSERT INTO ftth_devices (
                    id, name, node_type, sw_id, brand, model,
                    serial_no, mac, ip, split_ratio, split_group,
                    vlan, lat1, long1, remarks, position_x, position_y, position_mode,
                    area_id
                ) VALUES (
                    ftth_devices_sq.NEXTVAL, :name, :node_type, :sw_id, :brand, :model,
                    :serial_no, :mac, :ip, :split_ratio, :split_group,
                    :vlan, :lat1, :long1, :remarks, :position_x, :position_y, 0,
                    v_parent_area_id
                ) RETURNING id INTO v_new_device_id;
                
                -- Step 4: Create the new edge (Parent -> New Device)
                -- We also copy the cable color from the original edge
                INSERT INTO ftth_edges (
                    id, source_id, target_id, link_type, cable_color
                ) VALUES (
                    ftth_edges_sq.NEXTVAL, :original_source_id, v_new_device_id, :link_type, :cable_color
                );

                -- Step 5: Update the original edge to point to the new device
                -- (New Device -> Original Child)
                -- original_edge_record_id is the ID of the *child device*
                UPDATE ftth_edges
                SET source_id = v_new_device_id
                WHERE source_id = :original_source_id
                  AND target_id = :original_edge_record_id;

                -- Step 6: Reset positions
                UPDATE ftth_devices
                SET position_x = NULL,
                    position_y = NULL
                WHERE
                    (
                        id = v_new_device_id
                        OR
                        id IN (
                            SELECT d.id FROM ftth_devices d
                            START WITH d.id = :original_edge_record_id
                            CONNECT BY PRIOR d.id = (SELECT e.source_id FROM ftth_edges e WHERE e.target_id = d.id FETCH FIRST 1 ROW ONLY)
                        )
                    )
                    AND (position_mode IS NULL OR position_mode != 1);

                COMMIT;
            ELSE
                RAISE_APPLICATION_ERROR(-20001, 'Permission denied. Parent component must be in your area.');
            END IF;
        END;
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        params = insert_data.new_node_data

        all_node_keys = [
            "name",
            "node_type",
            "sw_id",
            "link_type",
            "brand",
            "model",
            "serial_no",
            "mac",
            "ip",
            "split_ratio",
            "split_group",
            "cable_id",
            "cable_start",
            "cable_end",
            "cable_length",
            "cable_color",
            "cable_desc",
            "vlan",
            "lat1",
            "long1",
            "remarks",
            "position_x",
            "position_y",
        ]
        for key in all_node_keys:
            params.setdefault(key, None)

        params["area_id"] = current_user.area_id
        params["original_source_id"] = insert_data.original_source_id
        params["original_edge_record_id"] = insert_data.original_edge_record_id

        cursor.execute(plsql_block, params)

        return {"message": "Node inserted successfully."}

    except oracledb.DatabaseError as e:
        if conn:
            conn.rollback()
        (error,) = e.args
        if "Permission denied" in error.message:
            raise HTTPException(
                status_code=403,
                detail="Permission denied. Parent component must be in your area.",
            )
        raise HTTPException(status_code=500, detail=f"Database transaction failed: {e}")
    finally:
        if conn:
            conn.close()


@app.post("/device", status_code=201, response_model=Dict[str, Any])
def create_device(node: NodeCreate, current_user: User = Depends(get_current_user)):
    """
    Creates a new device (orphan) in the database.
    --- UPDATED ---
    """
    node_data = node.dict(exclude_unset=True)
    if "node_name" in node_data:
        node_data["name"] = node_data.pop("node_name")
    if "device" in node_data:
        node_data["node_type"] = node_data.pop("device")

    if current_user.role_id not in [2, 3]:
        raise HTTPException(
            status_code=403, detail="Not authorized to create components."
        )
    if current_user.area_id is None:
        raise HTTPException(
            status_code=403, detail="Your account is not assigned to an area."
        )
    node_data["area_id"] = current_user.area_id

    device_cols = [
        "name",
        "node_type",
        "sw_id",
        "brand",
        "model",
        "serial_no",
        "mac",
        "ip",
        "split_ratio",
        "split_group",
        "vlan",
        "lat1",
        "long1",
        "remarks",
        "area_id",
        "position_mode",
    ]

    columns = []
    bind_vars = []
    params = {}

    for col in device_cols:
        if col in node_data:
            columns.append(col)
            bind_vars.append(f":{col}")
            params[col] = node_data[col]

    if "area_id" not in params:
        columns.append("area_id")
        bind_vars.append(":area_id")
        params["area_id"] = current_user.area_id

    if "position_mode" not in params:
        columns.append("position_mode")
        bind_vars.append("0")

    sql = f"""
        INSERT INTO ftth_devices (id, {', '.join(columns)}) 
        VALUES (ftth_devices_sq.NEXTVAL, {', '.join(bind_vars)})
        RETURNING id INTO :new_id
    """

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        new_id_var = cursor.var(oracledb.NUMBER)
        params["new_id"] = new_id_var

        if "position_mode" not in node_data:
            sql = sql.replace(":position_mode", "0")

        cursor.execute(sql, params)
        new_node_id = int(new_id_var.getvalue()[0])

        selection_cols = """
          d.ID, d.NAME, d.NODE_TYPE, d.STATUS, d.SW_ID, d.POP_ID, d.VLAN,
          d.SPLIT_RATIO, d.SPLIT_COLOR_GRP, d.SPLIT_COLOR, d.COLOR_GROUP,
          d.CONTAINER_ID, d.AREA_ID, d.REMARKS, d.USER_ID, d.SERIAL_NO,
          d.BRAND, d.LAT1, d.LONG1, d.IP, d.MAC, d.DEVICE_TYPE, d.MODEL,
          d.SPLIT_GROUP, d.POSITION_X, d.POSITION_Y, d.POSITION_MODE,
          e.SOURCE_ID as PARENT_ID,
          e.ID as EDGE_ID,
          e.LINK_TYPE, e.CABLE_ID, e.CABLE_LENGTH, e.CABLE_COLOR,
          e.CABLE_START, e.CABLE_DESC, e.CABLE_END, e.PARENT_PORT, e.SW_PORT2
        """

        cursor.execute(
            f"""
            SELECT {selection_cols} 
            FROM ftth_devices d 
            LEFT JOIN ftth_edges e ON d.id = e.target_id
            WHERE d.id = :id_bv
        """,
            {"id_bv": new_node_id},
        )

        db_columns = [desc[0].lower() for desc in cursor.description]
        row = cursor.fetchone()
        if not row:
            conn.rollback()
            raise HTTPException(
                status_code=500, detail="Failed to retrieve newly created device."
            )
        new_node_obj = dict(zip(db_columns, row))
        conn.commit()
        return new_node_obj

    except oracledb.DatabaseError as e:
        if conn:
            conn.rollback()
        (error,) = e.args
        if error.code == 1:
            raise HTTPException(
                status_code=409,
                detail=f"A device with the same unique properties already exists.",
            )
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        if conn:
            conn.close()


@app.get("/olts")
def get_olts(current_user: User = Depends(get_current_user)):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        sql = "SELECT id, name, olt_type, ip FROM switches WHERE SW_TYPE = 'OLT'"
        params = {}

        if current_user.role_id not in [2, 3]:
            raise HTTPException(status_code=403, detail="Not authorized.")
        if current_user.area_id is None:
            raise HTTPException(
                status_code=403, detail="Your account is not assigned to an area."
            )

        sql += " AND area_id = :area_id"
        params["area_id"] = current_user.area_id
        cursor.execute(sql, params)

        rows = cursor.fetchall()
        columns = [desc[0].lower() for desc in cursor.description]
        cursor.close()

        olts = [dict(zip(columns, row)) for row in rows]
        return olts

    except oracledb.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        if conn:
            conn.close()


@app.put("/device", status_code=200)
def update_device(
    node_update: NodeUpdate, current_user: User = Depends(get_current_user)
):
    """
    Updates a device's properties.
    --- UPDATED ---
    Splits the update into FTTH_DEVICES and FTTH_EDGES.
    """
    update_data = node_update.dict(exclude_unset=True)

    if "original_name" not in update_data:
        raise HTTPException(
            status_code=400,
            detail="'original_name' is required to identify the device.",
        )

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # --- UPDATED ---
        # 1. Define which fields belong to which table
        device_fields = [
            "name",
            "node_type",
            "status",
            "pop_id",
            "vlan",
            "split_ratio",
            "split_color_grp",
            "split_color",
            "color_group",
            "container_id",
            "remarks",
            "user_id",
            "serial_no",
            "brand",
            "lat1",
            "long1",
            "ip",
            "mac",
            "device_type",
            "model",
            "split_group",
            "position_x",
            "position_y",
            "position_mode",
        ]
        edge_fields = [
            "link_type",
            "cable_id",
            "cable_length",
            "cable_color",
            "cable_start",
            "cable_desc",
            "cable_end",
            "parent_port",
            "sw_port2",
        ]

        fields_to_set_device = {
            k: v for k, v in update_data.items() if k in device_fields
        }
        fields_to_set_edge = {k: v for k, v in update_data.items() if k in edge_fields}

        if not fields_to_set_device and not fields_to_set_edge:
            return {"message": "No data fields were provided to update."}

        # 2. Find the device ID to update
        if current_user.area_id is None:
            raise HTTPException(
                status_code=403, detail="Your account is not assigned to an area."
            )

        sw_id_clause = (
            "SW_ID = :sw_id" if node_update.sw_id is not None else "SW_ID IS NULL"
        )
        params_find = {
            "original_name": node_update.original_name,
            "area_id": current_user.area_id,
        }
        if node_update.sw_id is not None:
            params_find["sw_id"] = node_update.sw_id

        cursor.execute(
            f"SELECT id FROM ftth_devices WHERE NAME = :original_name AND {sw_id_clause} AND area_id = :area_id",
            params_find,
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"No devices found with name '{node_update.original_name}' in your area.",
            )
        device_id = row[0]

        rows_affected_device = 0
        rows_affected_edge = 0

        # 3. Update FTTH_DEVICES if there are device fields
        if fields_to_set_device:
            set_clauses_device = [
                f"{key} = :{key}" for key in fields_to_set_device.keys()
            ]
            params_device = fields_to_set_device
            params_device["id"] = device_id

            sql_device = f"""
                UPDATE ftth_devices 
                SET {', '.join(set_clauses_device)}
                WHERE id = :id
            """
            cursor.execute(sql_device, params_device)
            rows_affected_device = cursor.rowcount

        # 4. Update FTTH_EDGES if there are edge fields
        if fields_to_set_edge:
            set_clauses_edge = [f"{key} = :{key}" for key in fields_to_set_edge.keys()]
            params_edge = fields_to_set_edge
            params_edge["target_id"] = device_id

            sql_edge = f"""
                UPDATE ftth_edges
                SET {', '.join(set_clauses_edge)}
                WHERE target_id = :target_id
            """
            cursor.execute(sql_edge, params_edge)
            rows_affected_edge = cursor.rowcount

        conn.commit()
        return {
            "message": f"Device '{node_update.original_name}' was updated successfully. ({rows_affected_device} device, {rows_affected_edge} edge)"
        }

    except oracledb.Error as e:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        if conn:
            conn.close()


@app.post("/device/copy", status_code=201)
def copy_device(copy_request: NodeCopy, current_user: User = Depends(get_current_user)):
    """
    Connects a device to a new parent by creating an edge.
    --- UPDATED ---
    """
    plsql_block = """
        DECLARE
          v_source_area_id ftth_devices.area_id%TYPE;
          v_parent_area_id ftth_devices.area_id%TYPE;
        BEGIN
          -- 1. Get Area IDs for permission check
          SELECT area_id INTO v_source_area_id
          FROM ftth_devices WHERE id = :source_node_id;
          
          SELECT area_id INTO v_parent_area_id
          FROM ftth_devices WHERE id = :new_parent_id;

          -- 2. Enforce Authorization
          IF (v_source_area_id = :area_id AND v_parent_area_id = :area_id) THEN
          
            -- 3. Create the new edge
            INSERT INTO ftth_edges (id, source_id, target_id)
            VALUES (ftth_edges_sq.NEXTVAL, :new_parent_id, :source_node_id);

            -- 4. Reset position of the child node being connected
            UPDATE ftth_devices
            SET position_x = NULL,
                position_y = NULL,
                position_mode = 0
            WHERE id = :source_node_id
              AND (position_mode IS NULL OR position_mode != 1);

            COMMIT;
            
          ELSE
            RAISE_APPLICATION_ERROR(-20001, 'Permission denied. Both components must be in your area.');
          END IF;
        END;
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        params = {
            "source_node_id": copy_request.source_node_id,  # This is the CHILD
            "new_parent_id": copy_request.new_parent_id,  # This is the PARENT
            "area_id": current_user.area_id,
        }

        cursor.execute(plsql_block, params)
        cursor.close()

        return {
            "message": f"Device {copy_request.source_node_id} successfully connected to parent {copy_request.new_parent_id}."
        }
    except oracledb.DatabaseError as e:
        (error,) = e.args
        if "Permission denied" in str(e):
            raise HTTPException(
                status_code=403,
                detail="Permission denied. Both components must be in your area.",
            )
        if error.code == 1:  # ORA-00001: unique constraint violated
            raise HTTPException(
                status_code=409,
                detail="This connection already exists.",
            )
        if error.code == 1403:  # No data found
            raise HTTPException(
                status_code=404,
                detail=f"Source or parent device not found.",
            )
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        if conn:
            conn.close()


@app.delete("/node", status_code=200)
def delete_device(
    node_info: NodeDeleteByName, current_user: User = Depends(get_current_user)
):
    """
    Deletes a device. Re-parents any children to the deleted device's parent.
    --- UPDATED ---
    """
    plsql_block = """
    DECLARE
        v_rows_deleted NUMBER := 0;
        v_device_id ftth_devices.id%TYPE;
        v_parent_id ftth_devices.id%TYPE;
    BEGIN
        -- Step 1: Find the device and its parent ID
        BEGIN
            SELECT d.id, e.source_id INTO v_device_id, v_parent_id
            FROM ftth_devices d
            LEFT JOIN ftth_edges e ON d.id = e.target_id
            WHERE d.NAME = :name_bv
              AND NVL(d.SW_ID, -1) = NVL(:sw_id_bv, -1)
              AND d.area_id = :area_id
            FETCH FIRST 1 ROWS ONLY;
        EXCEPTION
            WHEN NO_DATA_FOUND THEN
                v_device_id := NULL;
                v_parent_id := NULL;
        END;

        -- Step 2: If device exists, proceed
        IF v_device_id IS NOT NULL THEN
        
            -- Step 3: Re-parent its immediate children to its parent
            UPDATE ftth_edges
            SET source_id = v_parent_id
            WHERE source_id = v_device_id;

            -- Step 4: If there was a grandparent, trigger position reset
            IF v_parent_id IS NOT NULL THEN
                UPDATE ftth_devices
                SET position_x = NULL,
                    position_y = NULL
                WHERE id IN (
                    SELECT d.id FROM ftth_devices d
                    START WITH d.id = v_parent_id
                    CONNECT BY PRIOR d.id = (SELECT e.source_id FROM ftth_edges e WHERE e.target_id = d.id FETCH FIRST 1 ROW ONLY)
                )
                AND (position_mode IS NULL OR position_mode != 1);
            END IF;
            
            -- Step 5: Delete the target device.
            -- Edges pointing *to* it are deleted by ON DELETE CASCADE
            DELETE FROM ftth_devices
            WHERE id = v_device_id;

            v_rows_deleted := SQL%ROWCOUNT;
            
        END IF;

        IF v_rows_deleted = 0 THEN
            RAISE_APPLICATION_ERROR(-20002, 'No node found to delete.');
        END IF;

        COMMIT;
    END;
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        params = {
            "name_bv": node_info.name,
            "sw_id_bv": node_info.sw_id,
            "area_id": current_user.area_id,
        }
        cursor.execute(plsql_block, params)
        conn.commit()
        return {"message": f"Device '{node_info.name}' was deleted successfully."}
    except oracledb.DatabaseError as e:
        if conn:
            conn.rollback()
        (error,) = e.args
        if "No node found to delete" in error.message:
            raise HTTPException(
                status_code=404,
                detail=f"No device with name '{node_info.name}' found for the specified system.",
            )
        raise HTTPException(status_code=500, detail=f"Database transaction failed: {e}")
    finally:
        if conn:
            conn.close()


@app.delete("/edge", status_code=200)
def delete_edge(
    edge_info: EdgeDeleteByName, current_user: User = Depends(get_current_user)
):
    """
    Disconnects a device from its parent by deleting the edge.
    --- UPDATED ---
    """
    plsql_block = """
    DECLARE
        v_target_node_id ftth_devices.id%TYPE;
        v_edge_deleted NUMBER := 0;
    BEGIN
        -- Step 1: Find the area_id and ID of the specific node being disconnected.
        SELECT id INTO v_target_node_id
        FROM ftth_devices
        WHERE NAME = :name_bv
          AND NVL(SW_ID, -1) = NVL(:sw_id_bv, -1)
          AND area_id = :area_id
        FETCH FIRST 1 ROWS ONLY;

        -- Step 2: Delete the edge record
        DELETE FROM ftth_edges
        WHERE source_id = :source_id_bv
          AND target_id = v_target_node_id;
        
        v_edge_deleted := SQL%ROWCOUNT;

        -- Step 3: If we successfully deleted, reset positions
        IF v_edge_deleted > 0 THEN
            -- Step 3a: Reset positions for the SOURCE node and its descendants
            UPDATE ftth_devices
            SET position_x = NULL, position_y = NULL, position_mode = 0
            WHERE id IN (
                SELECT d.id FROM ftth_devices d
                START WITH d.id = :source_id_bv
                CONNECT BY PRIOR d.id = (SELECT e.source_id FROM ftth_edges e WHERE e.target_id = d.id FETCH FIRST 1 ROW ONLY)
            )
            AND (position_mode IS NULL OR position_mode != 1);

            -- Step 3b: Reset positions for the TARGET node and its descendants
            UPDATE ftth_devices
            SET position_x = NULL, position_y = NULL, position_mode = 0
            WHERE id IN (
                SELECT d.id FROM ftth_devices d
                START WITH d.id = v_target_node_id
                CONNECT BY PRIOR d.id = (SELECT e.source_id FROM ftth_edges e WHERE e.target_id = d.id FETCH FIRST 1 ROW ONLY)
            )
            AND (position_mode IS NULL OR position_mode != 1);

            COMMIT;
        ELSE
            -- If no edge was deleted, it means it didn't exist
            RAISE_APPLICATION_ERROR(-20002, 'No matching connection found to delete.');
        END IF;

    EXCEPTION
        -- This handles the case where the initial SELECT finds no matching device
        WHEN NO_DATA_FOUND THEN
            RAISE_APPLICATION_ERROR(-20002, 'No matching device found to disconnect.');
    END;
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        params = {
            "name_bv": edge_info.name,
            "source_id_bv": edge_info.source_id,
            "sw_id_bv": edge_info.sw_id,
            "area_id": current_user.area_id,
        }
        cursor.execute(plsql_block, params)
        return {
            "message": f"Connection to '{edge_info.name}' from parent {edge_info.source_id} removed."
        }
    except oracledb.DatabaseError as e:
        if conn:
            conn.rollback()
        (error,) = e.args
        if "Permission denied" in error.message:
            raise HTTPException(
                status_code=403,
                detail="Permission denied. You do not have ownership of this component.",
            )
        if (
            "No matching connection found" in error.message
            or "No matching device found" in error.message
        ):
            raise HTTPException(
                status_code=404,
                detail="The specified connection or device was not found.",
            )
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        if conn:
            conn.close()
