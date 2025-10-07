# main.py
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


@app.get("/data/{sw_id}")
def read_data(sw_id: int):
    """
    Endpoint to get data from the 'nodes' table using the get_data function from crud.py.
    """
    return get_data(sw_id=sw_id)

    # ADD THIS NEW ENDPOINT


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

            -- Step 2: Update the ORIGINAL connection record.
            -- Instead of connecting to the old target, it now becomes the new node's child.
            -- We find it by its unique ID.
            UPDATE nodes
            SET parent_id = v_new_node_id
            WHERE id = :original_edge_record_id;
            
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


@app.put("/device", status_code=200)
def update_device(node_update: NodeUpdate):
    update_data = node_update.dict(exclude_unset=True)

    if not update_data:
        raise HTTPException(status_code=400, detail="No update data provided.")

    # VALIDATION: For any update, we need the original_name and sw_id to identify the group of records.
    if "original_name" not in update_data or "sw_id" not in update_data:
        raise HTTPException(
            status_code=400,
            detail="Both 'original_name' and 'sw_id' are required to identify the device for any update.",
        )

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # --- UNIFIED UPDATE LOGIC ---
        # All updates (rename, color change, etc.) now apply to all records for the specified device.

        # 1. Prepare the fields to be set in the UPDATE statement.
        # We exclude the identifiers used in the WHERE clause.
        fields_to_set = {
            k: v for k, v in update_data.items() if k not in ["original_name", "sw_id"]
        }

        # If there are no actual fields to SET after filtering, there's nothing to do.
        if not fields_to_set:
            return {"message": "No data fields were provided to update."}

        set_clauses = [f"{key} = :{key}" for key in fields_to_set.keys()]

        # 2. Construct the SQL to update all matching records.
        sql = f"""
            UPDATE nodes 
            SET {', '.join(set_clauses)}
            WHERE NAME = :original_name 
              AND SW_ID = :sw_id
        """

        # 3. Prepare the parameters for the query.
        params = fields_to_set
        params["original_name"] = node_update.original_name
        params["sw_id"] = node_update.sw_id

        cursor.execute(sql, params)

        if cursor.rowcount == 0:
            conn.rollback()
            raise HTTPException(
                status_code=404,
                detail=f"No nodes found with name '{node_update.original_name}' for the specified OLT.",
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
    Connects a device to a new parent.
    If an orphaned record for the device exists, it updates it.
    Otherwise, it creates a new record for the connection.
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

          -- 2. Check if an orphaned record (parent_id is null) already exists.
          SELECT COUNT(*)
          INTO v_orphan_count
          FROM nodes
          WHERE name = v_name
            AND sw_id = v_sw_id
            AND parent_id IS NULL;

          -- 3. Decide whether to UPDATE the orphan or INSERT a new copy.
          IF v_orphan_count > 0 THEN
            -- An orphan exists, so just update it with the new parent.
            UPDATE nodes
            SET parent_id = :new_parent_id,
                position_x = NULL,
                position_y = NULL,
                position_mode = 0
            WHERE name = v_name
              AND sw_id = v_sw_id
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

          -- 4. NEW: Reset positions for all sibling nodes that are not manually positioned.
          -- This will cause the front-end layout algorithm to rearrange the entire group.
          -- It only affects nodes where position_mode is not 1 (e.g., 0 or NULL).
          UPDATE nodes
          SET position_x = NULL,
              position_y = NULL
          WHERE parent_id = :new_parent_id
            AND (position_mode != 1 OR position_mode IS NULL);

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
    Deletes all records for a node based on its name and the SW_ID of the OLT.
    This ensures all connections for that specific ONU are removed.
    """
    sql = "DELETE FROM nodes WHERE NAME = :name_bv AND SW_ID = :sw_id_bv"

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        params = {"name_bv": node_info.name, "sw_id_bv": node_info.sw_id}

        cursor.execute(sql, params)

        # cursor.rowcount will be > 0 if any records were deleted
        if cursor.rowcount == 0:
            conn.rollback()
            raise HTTPException(
                status_code=404,
                detail=f"No node with name '{node_info.name}' found for the specified OLT (SW_ID: {node_info.sw_id}).",
            )

        conn.commit()

        return {
            "message": f"All records for node '{node_info.name}' under OLT {node_info.sw_id} were deleted successfully."
        }

    except oracledb.Error as e:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        if conn:
            conn.close()


@app.delete("/edge", status_code=200)
def delete_edge(edge_info: EdgeDeleteByName):
    """
    Disconnects a node from a parent using a PL/SQL block to handle unique constraints.
    It first deletes any existing orphaned record for the node and then sets the
    parent_id of the target connection to NULL.
    """
    # This PL/SQL block ensures the operations are atomic.
    plsql_block = """
    BEGIN
        -- Step 1: Delete any pre-existing orphaned record for this device.
        -- This prevents a unique constraint violation in the next step.
        DELETE FROM nodes
        WHERE NAME = :name_bv
          AND SW_ID = :sw_id_bv
          AND PARENT_ID IS NULL;

        -- Step 2: Update the target connection, setting its PARENT_ID to NULL
        -- and clearing its position to make it a freshly orphaned node.
        -- MODIFIED: Added position_x and position_y to the SET clause.
        UPDATE nodes
        SET PARENT_ID = NULL,
            position_x = NULL,
            position_y = NULL
        WHERE NAME = :name_bv
          AND PARENT_ID = :source_id_bv
          AND SW_ID = :sw_id_bv;

        -- Check if the update operation actually changed a row.
        IF SQL%ROWCOUNT = 0 THEN
            -- If no rows were updated, it means the connection didn't exist.
            RAISE_APPLICATION_ERROR(-20001, 'No matching connection found to update.');
        END IF;

        -- Step 3: NEW - Reset positions for all former sibling nodes that are not manually positioned.
        -- This will cause the front-end layout algorithm to rearrange the remaining group.
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

        conn.commit()  # Commit the transaction

        return {
            "message": f"Connection to '{edge_info.name}' from parent {edge_info.source_id} removed."
        }

    except oracledb.DatabaseError as e:
        if conn:
            conn.rollback()  # Rollback on any database error

        (error,) = e.args
        # Catch the custom error we raised in the PL/SQL block
        if "No matching connection found" in error.message:
            raise HTTPException(
                status_code=404, detail="The specified connection record was not found."
            )

        # Handle all other database errors
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    finally:
        if conn:
            conn.close()
