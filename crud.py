from fastapi import FastAPI, HTTPException
from typing import List, Dict, Any
import oracledb
from database import get_connection


# In crud.py


def get_data(root_node_id: int = None) -> List[Dict[str, Any]]:
    """
    Fetches data from the 'nodes' table.
    - If root_node_id is provided, it fetches that node and all its descendants.
    - If root_node_id is None, it fetches the general network view.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        sql = ""
        params = {}

        if root_node_id is not None:
            # --- FIX: Use a hierarchical query for specific views ---
            sql = """
                -- Query 1: Get the main node and all of its descendants (no change here)
                SELECT * FROM nodes
                START WITH id = :root_node_id_bv
                CONNECT BY PRIOR id = parent_id
    
                UNION

                -- Query 2: Get all nodes that BELONG to an orphan tree in the same system
                SELECT * FROM nodes
                WHERE id IN (
                    -- This subquery finds the IDs of all nodes in every orphan tree
                    SELECT id FROM nodes
                    -- START WITH finds the top of each orphan chain in the correct system
                    START WITH parent_id IS NULL
                        AND sw_id = :root_node_id_bv
                    -- CONNECT BY travels down each of those chains to get all descendants
                    CONNECT BY PRIOR id = parent_id
                )
            """
            params["root_node_id_bv"] = root_node_id
        else:
            # Logic for the general network view (unchanged)
            sql = """
                SELECT * FROM nodes
                WHERE node_type NOT IN ('PON', 'ONU') AND (parent_id IS NULL OR parent_id NOT IN (
                    SELECT id FROM nodes
                    WHERE node_type IN ('OLT', 'PON', 'ONU') AND sw_id IS NOT NULL
                )) AND sw_id IS NULL
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
