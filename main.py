# main.py
from typing import Any, Dict, List
from fastapi import FastAPI, HTTPException, Request, Depends, status
from fastapi.security import OAuth2PasswordRequestForm  # <-- Add this
from datetime import timedelta  # <-- Add this
import oracledb
from fastapi.middleware.cors import CORSMiddleware
from database import get_connection
from crud import get_data
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
    get_current_user,  # <-- You will need this for other endpoints
)

# Create the FastAPI application instance
app = FastAPI(title="netdiag-backend", version="1.0.0")

allowed_hosts = ["http://localhost:5173"]

# 2. REMOVE your old @app.middleware("http") function completely.

# 3. ADD the new CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_hosts,
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)


@app.get("/")
def read_root():
    """
    A simple root endpoint to confirm the API is running.
    """
    return {"message": "FastAPI is running. Visit /token to login."}


@app.post("/token", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    """
    Login endpoint to verify username/password and issue a JWT.
    """
    # 1. Get the password from DB (in plaintext, as per your setup)
    hashed_password = get_user_password_from_db(form_data.username)

    # 2. Verify the password
    # ⚠️ This uses PlainTextContext. See auth.py security note.
    if not hashed_password or not pwd_context.verify(
        form_data.password, hashed_password
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. Fetch the full user details to embed in the token
    user = get_user_from_db(form_data.username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not find user after login.",
        )

    # 4. Create the JWT
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


# Add this new function anywhere in main.py
@app.get(
    "/onu/{olt_id}/{port_name:path}/customers", response_model=List[OnuCustomerInfo]
)
def get_onu_customer_details(
    olt_id: int, port_name: str, current_user: User = Depends(get_current_user)
):
    """
    Fetches customer details for a specific ONU port on a given OLT.
    Includes authorization check to ensure reseller owns the OLT.
    """

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # --- UNIFIED AUTHORIZATION CHECK ---
        if current_user.role_id in [2, 3]:
            # User must have an assigned area to proceed
            if current_user.area_id is None:
                raise HTTPException(
                    status_code=403, detail="Your account is not assigned to an area."
                )

            cursor.execute(
                "SELECT area_id FROM nodes WHERE id = :olt_id_bv",
                {"olt_id_bv": olt_id},
            )
            row = cursor.fetchone()

            if not row or row[0] != current_user.area_id:
                raise HTTPException(
                    status_code=403,
                    detail="Permission denied: You do not own this OLT.",
                )
        else:
            # Deny any other role
            raise HTTPException(status_code=403, detail="Not authorized.")

        # --- Main Query (proceeds if user is admin or passed the check) ---
        sql = """
            SELECT port, portno, get_customer_id (h.user_id) cid, get_username (h.user_id) uname,
                   expiry_date, m.mac, get_full_name (owner_id) owner, h.status, ls,
                   nvl(class_id,-1) cls, is_online3 (h.user_id) online1, GET_USER_STATUS(h.user_id) st2,
                   sysdate-m.udate diff
            FROM OLT_CUSTOMER_MAC_2 m, switch_snmp_onu_ports p, home_conn h
            WHERE h.user_id=m.user_id
              AND p.ifdescr=m.port
              AND m.olt_id=p.sw_id
              AND m.olt_id = :olt_id_bv
              AND m.port = :port_name_bv
            ORDER BY m.port, portno
        """

        params = {"olt_id_bv": olt_id, "port_name_bv": port_name}
        cursor.execute(sql, params)

        # Map results to a list of dictionaries
        columns = [desc[0].lower() for desc in cursor.description]
        rows = cursor.fetchall()

        results = [dict(zip(columns, row)) for row in rows]
        return results

    except oracledb.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        if conn:
            conn.close()


# Add this new endpoint function anywhere inside main.py
@app.post("/positions/reset", status_code=200)
def reset_node_positions(
    reset_request: PositionReset, current_user: User = Depends(get_current_user)
):
    """
    Resets node positions based on the provided scope. Now correctly scopes the general view.
    """
    base_sql = """
        UPDATE nodes
        SET position_x = NULL,
            position_y = NULL,
            position_mode = 0
    """
    where_clauses = []
    params = {}

    # --- Authorization ---
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
            # Scope is a specific OLT system
            where_clauses.append("sw_id = :sw_id")
            params["sw_id"] = reset_request.sw_id
        else:
            # --- THIS IS THE FIX ---
            # Scope is the general view. Use the same logic as the GET /data endpoint.
            general_view_clause = """
            (node_type NOT IN ('PON', 'ONU') AND (parent_id IS NULL OR parent_id NOT IN (
                SELECT id FROM nodes
                WHERE node_type IN ('OLT', 'PON', 'ONU') AND sw_id IS NOT NULL
            )))
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
            return {"message": "No nodes matched the criteria for reset."}

        return {"message": f"{cursor.rowcount} node positions were reset."}
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
    Calls get_data without a root_node_id.
    """
    # --- THIS IS THE FIX ---
    # The function now expects 'root_node_id', not 'sw_id'.
    return get_data(root_node_id=None, current_user=current_user)


@app.get("/data/{root_node_id}")
def read_data(root_node_id: int, current_user: User = Depends(get_current_user)):
    """
    Endpoint to get a specific node and all its descendants.
    """
    return get_data(root_node_id=root_node_id, current_user=current_user)


@app.get("/nodes/root-candidates", response_model=List[Dict[str, Any]])
def get_root_candidates(current_user: User = Depends(get_current_user)):
    """
    Returns a list of nodes that can be used as a root, filtered by the
    user's area_id.
    """
    # --- UNIFIED AUTHORIZATION CHECK ---
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

        # Define the base SQL query first
        sql = """
            SELECT id, name FROM nodes 
            WHERE node_type IN ('Router', 'Managed Switch', 'Unmanaged Switch')
        """
        # Define the params dictionary
        params = {}

        # Apply the area_id filter for all authorized users
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
    Inserts a new node into an existing connection by updating the original
    connection record to point to the new node.
    """
    plsql_block = """
        DECLARE
            v_new_node_id nodes.id%TYPE;
            v_area_id     nodes.area_id%TYPE;
        BEGIN
            -- Step 1: Check permissions by getting area_id of the parent
            SELECT area_id INTO v_area_id
            FROM nodes
            WHERE id = :original_source_id;

            -- Step 2: Enforce authorization
            IF (v_parent_area_id = :area_id) THEN
                -- Step 3: Insert the new node, inheriting the parent's area_id
                INSERT INTO nodes (
                    id, name, node_type, parent_id, sw_id, link_type, brand, model, 
                    serial_no, mac, ip, split_ratio, split_group, cable_id, 
                    cable_start, cable_end, cable_length, cable_color, cable_desc, 
                    vlan, lat1, long1, remarks, position_x, position_y,
                    area_id -- <-- Add area_id
                ) VALUES (
                    nodes_sq.NEXTVAL, :name, :node_type, :parent_id, :sw_id, :link_type, :brand, :model,
                    :serial_no, :mac, :ip, :split_ratio, :split_group, :cable_id,
                    :cable_start, :cable_end, :cable_length, :cable_color, :cable_desc,
                    :vlan, :lat1, :long1, :remarks, :position_x, :position_y,
                    v_area_id -- <-- Use parent's area_id
                ) RETURNING id INTO v_new_node_id;

                -- Step 4: Update the ORIGINAL connection record
                UPDATE nodes
                SET parent_id = v_new_node_id
                WHERE id = :original_edge_record_id;

                -- Step 5: Reset positions (your existing logic)
                UPDATE nodes
                SET position_x = NULL,
                    position_y = NULL
                WHERE
                    (
                        parent_id = :parent_id
                        OR
                        id IN (
                            SELECT id FROM nodes
                            START WITH id = :original_edge_record_id
                            CONNECT BY PRIOR id = parent_id
                        )
                    )
                    AND (position_mode IS NULL OR position_mode != 1);
                
                COMMIT;
            
            ELSE
                RAISE_APPLICATION_ERROR(-20001, 'Permission denied.');
            END IF;
        END;
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        params = insert_data.new_node_data

        # Corrected list of all possible keys
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
        params["parent_id"] = insert_data.original_source_id
        params["original_edge_record_id"] = (
            insert_data.original_edge_record_id
        )  # Use the new param

        cursor.execute(plsql_block, params)
        conn.commit()

        return {"message": "Node inserted successfully."}

    except oracledb.DatabaseError as e:
        if conn:
            conn.rollback()
        if "Permission denied" in str(e):
            raise HTTPException(
                status_code=403, detail="Permission denied to modify this component."
            )
        raise HTTPException(status_code=500, detail=f"Database transaction failed: {e}")
    finally:
        if conn:
            conn.close()


@app.post("/device", status_code=201)
def create_node(node: NodeCreate, current_user: User = Depends(get_current_user)):
    """
    Creates a new node in the database.
    """
    # Rename 'node_name' from form to 'name' for the database
    node_data = node.dict(exclude_unset=True)
    if "node_name" in node_data:
        node_data["name"] = node_data.pop("node_name")
    if "device" in node_data:
        node_data["node_type"] = node_data.pop("device")

    # --- Simplified Authorization Logic ---
    if current_user.role_id not in [2, 3]:
        raise HTTPException(
            status_code=403, detail="Not authorized to create components."
        )

    if current_user.area_id is None:
        raise HTTPException(
            status_code=403, detail="Your account is not assigned to an area."
        )

    # Force the new node to be created in the user's assigned area.
    node_data["area_id"] = current_user.area_id

    # Prepare SQL statement by dynamically getting columns and bind variables
    columns = node_data.keys()
    bind_vars = [f":{col}" for col in columns]

    # --- Make sure 'area_id' is included in the INSERT
    if "area_id" not in columns:
        columns.append("area_id")
        bind_vars.append(":area_id")

    sql = f"""
        INSERT INTO nodes (id, {', '.join(columns)}) 
        VALUES (nodes_sq.NEXTVAL, {', '.join(bind_vars)})
    """

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(sql, node_data)
        conn.commit()

        return {"message": f"Node '{node.name}' created successfully."}

    except oracledb.DatabaseError as e:
        if conn:
            conn.rollback()
        # Check for unique constraint violation
        (error,) = e.args
        if error.code == 1:
            raise HTTPException(
                status_code=409,
                detail=f"A node with the same unique properties already exists.",
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
        # Query to get all switches of type 'OLT'
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

        # Fetch all results and column names
        rows = cursor.fetchall()
        columns = [desc[0].lower() for desc in cursor.description]
        cursor.close()

        # Create a list of dictionaries
        olts = [dict(zip(columns, row)) for row in rows]
        return olts

    except oracledb.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        if conn:
            conn.close()


# In main.py


@app.put("/device", status_code=200)
def update_device(
    node_update: NodeUpdate, current_user: User = Depends(get_current_user)
):
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

        # This line correctly prevents area_id from ever being updated.
        fields_to_set = {
            k: v
            for k, v in update_data.items()
            if k not in ["original_name", "sw_id", "area_id"]
        }

        if not fields_to_set:
            return {"message": "No data fields were provided to update."}

        set_clauses = [f"{key} = :{key}" for key in fields_to_set.keys()]

        sw_id_clause = (
            "SW_ID = :sw_id" if node_update.sw_id is not None else "SW_ID IS NULL"
        )

        params = fields_to_set

        # --- UNIFIED AUTHORIZATION ---
        # Ensures the user can only update a device within their own area.
        if current_user.role_id not in [2, 3]:
            raise HTTPException(status_code=403, detail="Not authorized.")
        if current_user.area_id is None:
            raise HTTPException(
                status_code=403, detail="Your account is not assigned to an area."
            )

        auth_clause = " AND area_id = :area_id"
        params["area_id"] = current_user.area_id

        # Add remaining identifiers to params
        params["original_name"] = node_update.original_name
        if node_update.sw_id is not None:
            params["sw_id"] = node_update.sw_id

        sql = f"""
            UPDATE nodes 
            SET {', '.join(set_clauses)}
            WHERE NAME = :original_name 
              AND {sw_id_clause}
              {auth_clause} 
        """

        cursor.execute(sql, params)

        if cursor.rowcount == 0:
            conn.rollback()
            # More specific error message
            raise HTTPException(
                status_code=404,
                detail=f"No nodes found with name '{node_update.original_name}' in your area.",
            )

        conn.commit()
        return {
            "message": f"Device '{node_update.original_name}' was updated successfully."
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
    Connects a device to a new parent using a null-safe PL/SQL block.
    If an orphaned record for the device exists, it updates it.
    Otherwise, it creates a new record for the connection.
    Also resets positions for the new sibling group, ignoring manually placed nodes.
    """
    plsql_block = """
        DECLARE
          v_orphan_count NUMBER;
          v_name         nodes.name%TYPE;
          v_sw_id        nodes.sw_id%TYPE;
          node_record    nodes%ROWTYPE;
          v_source_area_id nodes.area_id%TYPE;
          v_parent_area_id nodes.area_id%TYPE;
        BEGIN
          -- 1. Get Area IDs for permission check
          SELECT area_id INTO v_source_area_id
          FROM nodes WHERE id = :source_node_id;
          
          SELECT area_id INTO v_parent_area_id
          FROM nodes WHERE id = :new_parent_id;

          -- 2. Enforce Authorization
          IF (v_source_area_id = :area_id AND v_parent_area_id = :area_id) THEN
          
            -- 3. Find the name and sw_id of the source device
            SELECT name, sw_id
            INTO v_name, v_sw_id
            FROM nodes
            WHERE id = :source_node_id;

            -- 4. Check for an orphan
            SELECT COUNT(*)
            INTO v_orphan_count
            FROM nodes
            WHERE name = v_name
              AND NVL(sw_id, -1) = NVL(v_sw_id, -1)
              AND parent_id IS NULL;

            -- 5. Decide whether to UPDATE or INSERT
            IF v_orphan_count > 0 THEN
              -- Update orphan
              UPDATE nodes
              SET parent_id = :new_parent_id,
                  position_x = NULL,
                  position_y = NULL,
                  position_mode = 0,
                  area_id = v_parent_area_id -- <-- Inherit new parent's area
              WHERE name = v_name
                AND NVL(sw_id, -1) = NVL(v_sw_id, -1)
                AND parent_id IS NULL;
            ELSE
              -- Create new copy
              SELECT *
              INTO node_record
              FROM nodes
              WHERE id = :source_node_id;

              node_record.id := nodes_sq.NEXTVAL;
              node_record.parent_id := :new_parent_id;
              node_record.area_id := v_parent_area_id; -- <-- Inherit new parent's area

              INSERT INTO nodes VALUES node_record;
            END IF;

            -- 6. Reset positions (your existing logic)
            UPDATE nodes
            SET position_x = NULL,
                position_y = NULL
            WHERE id IN (
                SELECT id FROM nodes
                START WITH parent_id = :new_parent_id
                CONNECT BY PRIOR id = parent_id
              )
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
            "source_node_id": copy_request.source_node_id,
            "new_parent_id": copy_request.new_parent_id,
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
        if error.code == 1403:
            raise HTTPException(
                status_code=404,
                detail=f"Source device with ID {copy_request.source_node_id} not found.",
            )
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        if conn:
            conn.close()


@app.delete("/node", status_code=200)
def delete_node(
    node_info: NodeDeleteByName, current_user: User = Depends(get_current_user)
):
    """
    Deletes a node. Before deleting, it re-parents any children to the
    deleted node's parent. It then triggers a CASCADING position reset
    for the entire affected branch, starting from the grandparent.
    """
    plsql_block = """
    DECLARE
        v_rows_deleted NUMBER := 0;
    BEGIN
        -- Step 1: For every instance of the node we are deleting...
        FOR node_to_delete IN (
            SELECT id, parent_id
            FROM nodes
            WHERE NAME = :name_bv
              AND NVL(SW_ID, -1) = NVL(:sw_id_bv, -1)
              AND (area_id = :area_id)
        )
        LOOP
            -- Step 2: Re-parent its immediate children to its parent (the "grandparent").
            -- This patches the chain (e.g., 1-2-3 becomes 1-3).
            UPDATE nodes
            SET parent_id = node_to_delete.parent_id
            WHERE parent_id = node_to_delete.id;

            -- --- THIS IS THE FIX ---
            -- Step 3: If there was a grandparent, trigger a cascading position reset
            -- for that grandparent and its entire descendant tree.
            IF node_to_delete.parent_id IS NOT NULL THEN
                UPDATE nodes
                SET position_x = NULL,
                    position_y = NULL
                WHERE id IN (
                    -- Find the grandparent itself and all of its descendants
                    SELECT id FROM nodes
                    START WITH id = node_to_delete.parent_id
                    CONNECT BY PRIOR id = parent_id
                )
                -- Only reset nodes that were not manually positioned.
                AND (position_mode IS NULL OR position_mode != 1);
            END IF;

        END LOOP;

        -- Step 4: After processing all instances, delete the target node.
        DELETE FROM nodes
        WHERE NAME = :name_bv
          AND NVL(SW_ID, -1) = NVL(:sw_id_bv, -1)
          AND (area_id = :area_id);

        -- Step 5: Check if the delete operation actually removed rows.
        v_rows_deleted := SQL%ROWCOUNT;
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

        return {
            "message": f"All records for node '{node_info.name}' were deleted successfully."
        }

    except oracledb.DatabaseError as e:
        if conn:
            conn.rollback()
        (error,) = e.args
        if "No node found to delete" in error.message:
            raise HTTPException(
                status_code=404,
                detail=f"No node with name '{node_info.name}' found for the specified system.",
            )
        raise HTTPException(status_code=500, detail=f"Database transaction failed: {e}")
    finally:
        if conn:
            conn.close()


# In main.py, find and replace the entire /edge delete endpoint function


@app.delete("/edge", status_code=200)
def delete_edge(
    edge_info: EdgeDeleteByName, current_user: User = Depends(get_current_user)
):
    """
    Disconnects a node from its parent by setting its parent_id to NULL.
    - Admins can disconnect any node.
    - Resellers can only disconnect nodes within their own area_id.
    """
    # This PL/SQL block now includes the authorization check.
    # The operation is atomic: if the user lacks permission, no changes are made.
    plsql_block = """
    DECLARE
        v_child_area_id nodes.area_id%TYPE;
    BEGIN
        -- Step 1: Find the area_id of the specific node being disconnected to check permissions.
        -- We must find the exact record representing the connection to be broken.
        SELECT area_id INTO v_child_area_id
        FROM nodes
        WHERE NAME = :name_bv
          AND PARENT_ID = :source_id_bv
          AND NVL(SW_ID, -1) = NVL(:sw_id_bv, -1)
        FETCH FIRST 1 ROWS ONLY; -- Ensures we only get one row

        -- Step 2: Enforce Authorization.
        -- The operation proceeds only if the user is an admin OR the component's area matches the reseller's area.
        IF (v_child_area_id = :area_id) THEN

            -- Step 3: Delete any pre-existing orphan to prevent conflicts.
            DELETE FROM nodes
            WHERE NAME = :name_bv
              AND NVL(SW_ID, -1) = NVL(:sw_id_bv, -1)
              AND PARENT_ID IS NULL;

            -- Step 4: Make the target node an orphan by setting its PARENT_ID to NULL.
            UPDATE nodes
            SET PARENT_ID = NULL
            WHERE NAME = :name_bv
              AND PARENT_ID = :source_id_bv
              AND NVL(SW_ID, -1) = NVL(:sw_id_bv, -1);

            -- Step 5: Perform a CASCADING position reset on the newly created orphan tree.
            UPDATE nodes
            SET position_x = NULL,
                position_y = NULL
            WHERE id IN (
                SELECT id FROM nodes
                START WITH NAME = :name_bv AND NVL(SW_ID, -1) = NVL(:sw_id_bv, -1) AND PARENT_ID IS NULL
                CONNECT BY PRIOR id = parent_id
            )
            AND (position_mode IS NULL OR position_mode != 1);

            -- Step 6: Reset positions for the former siblings that remained connected.
            UPDATE nodes
            SET position_x = NULL,
                position_y = NULL
            WHERE PARENT_ID = :source_id_bv
              AND (position_mode != 1 OR position_mode IS NULL);
              
            COMMIT; -- Commit the transaction only on success

        ELSE
            -- If the authorization check fails, raise a custom error.
            RAISE_APPLICATION_ERROR(-20001, 'Permission denied to modify this component.');
        END IF;

    EXCEPTION
        -- This handles the case where the initial SELECT finds no matching edge.
        WHEN NO_DATA_FOUND THEN
            RAISE_APPLICATION_ERROR(-20002, 'No matching connection found to delete.');
    END;
    """

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # Pass all necessary parameters, including role and area for authorization
        params = {
            "name_bv": edge_info.name,
            "source_id_bv": edge_info.source_id,
            "sw_id_bv": edge_info.sw_id,
            "area_id": current_user.area_id,
        }

        cursor.execute(plsql_block, params)
        # The COMMIT is now handled inside the successful PL/SQL block

        return {
            "message": f"Connection to '{edge_info.name}' from parent {edge_info.source_id} removed."
        }

    except oracledb.DatabaseError as e:
        if conn:
            conn.rollback()

        (error,) = e.args
        # Handle the specific custom errors raised from the PL/SQL block
        if "Permission denied" in error.message:
            raise HTTPException(
                status_code=403,
                detail="Permission denied. You do not have ownership of this component.",
            )
        if "No matching connection found" in error.message:
            raise HTTPException(
                status_code=404, detail="The specified connection record was not found."
            )

        # General fallback for other database errors
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    finally:
        if conn:
            conn.close()
