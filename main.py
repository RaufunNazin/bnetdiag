# main.py
from typing import Any, Dict, List
from fastapi import FastAPI, HTTPException, Request
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
    return {"message": "FastAPI is running. Visit /test-oracle to query the database."}


@app.get("/test-oracle")
def test_oracle_connection():
    """
    Gets a connection from the database module, executes a simple query,
    and returns the result.
    """
    conn = None  # Initialize connection to None
    try:
        # Get a connection using the new function
        conn = get_connection()
        print("✅ Connection successful!")

        # Create a cursor to execute SQL commands
        cursor = conn.cursor()

        # Define and execute the query
        sql = "SELECT user, sysdate FROM dual"
        print(f"Executing query: {sql}")
        cursor.execute(sql)

        # Fetch the result
        result = cursor.fetchone()
        cursor.close()

        if result:
            db_user, db_date = result
            return {
                "status": "success",
                "database_user": "db_user",
                "database_time": db_date.strftime("%Y-%m-%d %H:%M:%S"),
            }
        else:
            raise HTTPException(status_code=404, detail="Query returned no results.")

    except oracledb.Error as e:
        # Handle any database-related errors
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    except Exception as e:
        # Handle other unexpected errors
        raise HTTPException(
            status_code=500, detail=f"An unexpected error occurred: {e}"
        )

    finally:
        # VERY IMPORTANT: Ensure the connection is always closed
        if conn:
            conn.close()
            print("Connection closed.")


# Add this new function anywhere in main.py
@app.get("/onu/{olt_id}/{port_name:path}/customers", response_model=List[OnuCustomerInfo])
def get_onu_customer_details(olt_id: int, port_name: str):
    """
    Fetches customer details for a specific ONU port on a given OLT.
    """
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
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

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
def reset_node_positions(reset_request: PositionReset):
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
async def read_general_data():
    """
    Endpoint for the general network view.
    Calls get_data without a root_node_id.
    """
    # --- THIS IS THE FIX ---
    # The function now expects 'root_node_id', not 'sw_id'.
    return get_data(root_node_id=None)


@app.get("/data/{root_node_id}")
def read_data(root_node_id: int):
    """
    Endpoint to get a specific node and all its descendants.
    """
    return get_data(root_node_id=root_node_id)


@app.get("/nodes/root-candidates", response_model=List[Dict[str, Any]])
def get_root_candidates():
    """
    Returns a list of nodes that can be used as a root in the general view
    (Routers and Switches).
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        sql = """
            SELECT id, name FROM nodes 
            WHERE node_type IN ('Router', 'Managed Switch', 'Unmanaged Switch') 
            ORDER BY name
        """
        cursor.execute(sql)
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
def insert_node(insert_data: NodeInsert):
    """
    Inserts a new node into an existing connection by updating the original
    connection record to point to the new node.
    """
    plsql_block = """
        DECLARE
            v_new_node_id nodes.id%TYPE;
        BEGIN
            -- Step 1: Insert the new node. Its parent is the original source.
            INSERT INTO nodes (
                id, name, node_type, parent_id, sw_id, link_type, brand, model, 
                serial_no, mac, ip, split_ratio, split_group, cable_id, 
                cable_start, cable_end, cable_length, cable_color, cable_desc, 
                vlan, lat1, long1, remarks, position_x, position_y
            ) VALUES (
                nodes_sq.NEXTVAL, :name, :node_type, :parent_id, :sw_id, :link_type, :brand, :model,
                :serial_no, :mac, :ip, :split_ratio, :split_group, :cable_id,
                :cable_start, :cable_end, :cable_length, :cable_color, :cable_desc,
                :vlan, :lat1, :long1, :remarks, :position_x, :position_y
            ) RETURNING id INTO v_new_node_id;

            -- Step 2: Update the ORIGINAL connection record to point to the new node.
            UPDATE nodes
            SET parent_id = v_new_node_id
            WHERE id = :original_edge_record_id;

            -- --- THIS IS THE FIX ---
            -- Step 3: Reset positions for all sibling nodes of the newly inserted node.
            -- This forces the frontend layout algorithm to rearrange the group.
            UPDATE nodes
            SET position_x = NULL,
                position_y = NULL
            WHERE
                (
                    -- Group (a): Siblings of the newly inserted node
                    parent_id = :parent_id
                    OR
                    -- Group (b): The original child and its entire descendant tree
                    id IN (
                        SELECT id FROM nodes
                        START WITH id = :original_edge_record_id
                        CONNECT BY PRIOR id = parent_id
                    )
                )
                AND (position_mode IS NULL OR position_mode != 1);

            
            COMMIT; -- Ensure the transaction is committed
            
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
        raise HTTPException(status_code=500, detail=f"Database transaction failed: {e}")
    finally:
        if conn:
            conn.close()


@app.post("/device", status_code=201)
def create_node(node: NodeCreate):
    """
    Creates a new node in the database.
    """
    # Rename 'node_name' from form to 'name' for the database
    node_data = node.dict(exclude_unset=True)
    if "node_name" in node_data:
        node_data["name"] = node_data.pop("node_name")
    if "device" in node_data:
        node_data["node_type"] = node_data.pop("device")

    # Prepare SQL statement by dynamically getting columns and bind variables
    columns = node_data.keys()
    bind_vars = [f":{col}" for col in columns]

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
def get_olts():
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # Query to get all switches of type 'OLT'
        sql = "SELECT id, name, olt_type, ip FROM switches WHERE SW_TYPE = 'OLT'"
        cursor.execute(sql)

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
def update_device(node_update: NodeUpdate):
    update_data = node_update.dict(exclude_unset=True)

    # --- Start of Debugging ---
    print("\n--- UPDATE DEVICE REQUEST ---")
    print(f"Received data: {update_data}")
    # --- End of Debugging ---

    if "original_name" not in update_data:
        raise HTTPException(
            status_code=400,
            detail="'original_name' is required to identify the device.",
        )

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        fields_to_set = {
            k: v for k, v in update_data.items() if k not in ["original_name", "sw_id"]
        }

        if not fields_to_set:
            return {"message": "No data fields were provided to update."}

        set_clauses = [f"{key} = :{key}" for key in fields_to_set.keys()]

        sw_id_clause = (
            "SW_ID = :sw_id" if node_update.sw_id is not None else "SW_ID IS NULL"
        )

        sql = f"""
            UPDATE nodes 
            SET {', '.join(set_clauses)}
            WHERE NAME = :original_name 
              AND {sw_id_clause}
        """

        params = fields_to_set
        params["original_name"] = node_update.original_name
        if node_update.sw_id is not None:
            params["sw_id"] = node_update.sw_id

        # --- More Debugging ---
        print(f"Executing SQL: {sql.strip()}")
        print(f"With parameters: {params}")
        # --- End of Debugging ---

        cursor.execute(sql, params)

        # --- Final Debugging Check ---
        print(f"Rows affected: {cursor.rowcount}")
        # --- End of Debugging ---

        if cursor.rowcount == 0:
            conn.rollback()
            raise HTTPException(
                status_code=404,
                detail=f"No nodes found with name '{node_update.original_name}' for the specified system.",
            )

        conn.commit()
        return {
            "message": f"All records for device '{node_update.original_name}' were updated successfully."
        }

    except oracledb.Error as e:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        if conn:
            conn.close()


@app.post("/device/copy", status_code=201)
def copy_device(copy_request: NodeCopy):
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
        BEGIN
          -- 1. Find the name and sw_id of the source device to identify its group.
          SELECT name, sw_id
          INTO v_name, v_sw_id
          FROM nodes
          WHERE id = :source_node_id;

          -- 2. Check for an orphan using a null-safe comparison.
          SELECT COUNT(*)
          INTO v_orphan_count
          FROM nodes
          WHERE name = v_name
            AND NVL(sw_id, -1) = NVL(v_sw_id, -1)
            AND parent_id IS NULL;

          -- 3. Decide whether to UPDATE the orphan or INSERT a new copy.
          IF v_orphan_count > 0 THEN
            -- An orphan exists, so update it with the new parent (null-safe).
            UPDATE nodes
            SET parent_id = :new_parent_id,
                position_x = NULL,
                position_y = NULL,
                position_mode = 0
            WHERE name = v_name
              AND NVL(sw_id, -1) = NVL(v_sw_id, -1)
              AND parent_id IS NULL;
          ELSE
            -- No orphan exists, so create a new copy.
            SELECT *
            INTO node_record
            FROM nodes
            WHERE id = :source_node_id;

            node_record.id := nodes_sq.NEXTVAL;
            node_record.parent_id := :new_parent_id;

            INSERT INTO nodes VALUES node_record;
          END IF;

          -- --- THIS IS THE FIX ---
          -- 4. Reset positions for all sibling nodes of the new connection,
          --    ignoring any nodes that were manually positioned.
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
        END;
    """

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        params = {
            "source_node_id": copy_request.source_node_id,
            "new_parent_id": copy_request.new_parent_id,
        }

        cursor.execute(plsql_block, params)
        cursor.close()

        return {
            "message": f"Device {copy_request.source_node_id} successfully connected to parent {copy_request.new_parent_id}."
        }

    except oracledb.DatabaseError as e:
        (error,) = e.args
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
def delete_node(node_info: NodeDeleteByName):
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
          AND NVL(SW_ID, -1) = NVL(:sw_id_bv, -1);

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
def delete_edge(edge_info: EdgeDeleteByName):
    """
    Disconnects a node from a parent using a null-safe PL/SQL block.
    """
    plsql_block = """
    BEGIN
        -- Step 1: Delete any pre-existing orphan to prevent conflicts.
        DELETE FROM nodes
        WHERE NAME = :name_bv
          AND NVL(SW_ID, -1) = NVL(:sw_id_bv, -1)
          AND PARENT_ID IS NULL;

        -- Step 2: Make the target node an orphan by setting its PARENT_ID to NULL.
        -- We no longer reset the position here; we do it in the next step.
        UPDATE nodes
        SET PARENT_ID = NULL
        WHERE NAME = :name_bv
          AND PARENT_ID = :source_id_bv
          AND NVL(SW_ID, -1) = NVL(:sw_id_bv, -1);

        -- Check if the update operation actually changed a row.
        IF SQL%ROWCOUNT = 0 THEN
            RAISE_APPLICATION_ERROR(-20001, 'No matching connection found to update.');
        END IF;

        -- --- THIS IS THE FIX ---
        -- Step 3: Perform a CASCADING position reset on the newly created orphan tree.
        UPDATE nodes
        SET position_x = NULL,
            position_y = NULL
        WHERE id IN (
            -- Find the new orphan root and ALL of its descendants
            SELECT id FROM nodes
            START WITH NAME = :name_bv AND NVL(SW_ID, -1) = NVL(:sw_id_bv, -1) AND PARENT_ID IS NULL
            CONNECT BY PRIOR id = parent_id
        )
        AND (position_mode IS NULL OR position_mode != 1);

        -- Step 4: Reset positions for the former siblings that remained connected.
        UPDATE nodes
        SET position_x = NULL,
            position_y = NULL
        WHERE PARENT_ID = :source_id_bv
          AND (position_mode != 1 OR position_mode IS NULL);

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
        }

        cursor.execute(plsql_block, params)
        conn.commit()

        return {
            "message": f"Connection to '{edge_info.name}' from parent {edge_info.source_id} removed."
        }

    except oracledb.DatabaseError as e:
        if conn:
            conn.rollback()

        (error,) = e.args
        if "No matching connection found" in error.message:
            raise HTTPException(
                status_code=404, detail="The specified connection record was not found."
            )
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    finally:
        if conn:
            conn.close()
