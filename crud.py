from fastapi import FastAPI, HTTPException
from typing import List, Dict, Any
import oracledb
from database import get_connection

def get_data(sw_id: int = None) -> List[Dict[str, Any]]:
    """
    Fetches data from the 'nodes' table.
    - If sw_id is provided, it fetches all records for that sw_id.
    - If sw_id is None, it fetches all nodes that do NOT belong to an OLT-based system.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        sql = ""
        params = {}

        if sw_id is not None:
            # Logic for a specific OLT view (unchanged)
            sql = "SELECT * FROM nodes WHERE SW_ID = :sw_id_bv OR ID = :sw_id_bv"
            params["sw_id_bv"] = sw_id
        else:
            # --- CORRECTED LOGIC FOR THE GENERAL VIEW ---
            # This query selects nodes where:
            # 1. sw_id IS NULL
            # OR
            # 2. sw_id is NOT IN the list of sw_ids that belong to OLT systems.
            # The subquery explicitly excludes NULLs to ensure 'NOT IN' works correctly.
            sql = """
                SELECT * FROM nodes
                WHERE node_type NOT IN ('PON', 'ONU') AND (parent_id IS NULL OR parent_id NOT IN (
                    SELECT id FROM nodes
                    WHERE node_type IN ('OLT', 'PON', 'ONU') AND sw_id IS NOT NULL
                ))
            """

        cursor.execute(sql, params)
        columns = [desc[0].lower() for desc in cursor.description]
        rows = cursor.fetchall()
        cursor.close()

        if not rows:
            return []

        data = [dict(zip(columns, row)) for row in rows]
        return data

    except oracledb.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        if conn:
            conn.close()