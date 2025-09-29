# main.py
from fastapi import FastAPI, HTTPException, Request
import oracledb
from fastapi.middleware.cors import CORSMiddleware
from database import get_connection
from crud import get_data
from models import NodeUpdate, NodeCopy, EdgeDeleteByName, NodeDeleteByName

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


@app.put("/device/{device_id}")
def update_device(device_id: int, node_update: NodeUpdate):
    # 1. Convert the incoming data to a dictionary, excluding any fields that were not sent
    update_data = node_update.dict(exclude_unset=True)

    # If the request body is empty, there's nothing to update
    if not update_data:
        raise HTTPException(status_code=400, detail="No update data provided.")

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # 2. Dynamically build the SET part of the SQL query
        # This creates a list like ["name = :name", "cable_color = :cable_color"]
        set_clauses = [f"{key} = :{key}" for key in update_data.keys()]

        sql = f"UPDATE nodes SET {', '.join(set_clauses)} WHERE ID = :device_id_bv"

        # 3. Prepare the parameters for the query
        # This will look like {"name": "NEW NAME", "cable_color": "blue", "device_id_bv": 123}
        params = update_data.copy()
        params["device_id_bv"] = device_id

        # 4. Execute, commit, and check if a row was actually updated
        cursor.execute(sql, params)

        if cursor.rowcount == 0:
            conn.rollback()  # Rollback if no rows were affected
            raise HTTPException(
                status_code=404, detail=f"Device with ID {device_id} not found."
            )

        conn.commit()  # IMPORTANT: Commit the transaction to save changes

        return {"message": f"Device with ID {device_id} updated successfully."}

    except oracledb.Error as e:
        if conn:
            conn.rollback()  # Rollback on error
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        if conn:
            conn.close()


@app.post("/device/copy", status_code=201)
def copy_device(copy_request: NodeCopy):
    """
    Copies a node by its ID, assigning a new parent_id and generating a new unique ID.
    """
    plsql_block = """
        DECLARE
          node_record nodes%ROWTYPE;
        BEGIN
          -- 1. Select the entire source row into the record variable
          SELECT *
          INTO node_record
          FROM nodes
          WHERE ID = :source_node_id;

          -- 2. Modify the necessary fields for the new row
          node_record.ID := nodes_sq.NEXTVAL;
          node_record.PARENT_ID := :new_parent_id;

          -- 3. Insert the modified record as a new row
          INSERT INTO nodes VALUES node_record;
          
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
            "message": f"Device {copy_request.source_node_id} successfully copied to parent {copy_request.new_parent_id}."
        }

    except oracledb.DatabaseError as e:
        (error,) = e.args
        # This specifically checks for the "no data found" error from the SELECT...INTO
        if error.code == 1403:
            raise HTTPException(
                status_code=404,
                detail=f"Source device with ID {copy_request.source_node_id} not found.",
            )
        # For all other database errors, raise a 500
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

        -- Step 2: Update the target connection, setting its PARENT_ID to NULL.
        -- This effectively orphans the record, making it available for future connections.
        UPDATE nodes
        SET PARENT_ID = NULL
        WHERE NAME = :name_bv
          AND PARENT_ID = :source_id_bv
          AND SW_ID = :sw_id_bv;
          
        -- Check if the update operation actually changed a row.
        IF SQL%ROWCOUNT = 0 THEN
            -- If no rows were updated, it means the connection didn't exist.
            -- We raise an application error, which will be caught as an exception.
            RAISE_APPLICATION_ERROR(-20001, 'No matching connection found to update.');
        END IF;

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
