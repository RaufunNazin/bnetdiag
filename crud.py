from fastapi import FastAPI, HTTPException
import oracledb
from database import get_connection


def get_data(sw_id: int = None):
    """
    Fetches all columns from the 'nodes' table for a given sw_id (or all records if sw_id is None).
    It dynamically creates a list of dictionaries where keys are the column names.
    """
    conn = None
    try:
        conn = get_connection()
        print("✅ Connection successful!")
        cursor = conn.cursor()

        # 1. Base query to select all columns
        sql = "SELECT * FROM nodes"
        params = {}

        # Dynamically add the WHERE clause if sw_id is provided
        if sw_id is not None:
            sql += " WHERE SW_ID = :sw_id_bv"
            params["sw_id_bv"] = sw_id
            print(f"Executing query for SW_ID: {sw_id}")
        else:
            print("Executing query for all SW_IDs")

        cursor.execute(sql, params)

        # 2. Get the column names from the cursor description
        # We convert them to lowercase for consistent JSON keys
        columns = [desc[0].lower() for desc in cursor.description]

        # 3. Fetch all rows of data
        rows = cursor.fetchall()
        cursor.close()

        if not rows:
            return []  # Return an empty list if no results are found

        # 4. Create a list of dictionaries by zipping column names with row data
        data = [dict(zip(columns, row)) for row in rows]

        return data

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
