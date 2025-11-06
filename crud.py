from fastapi import HTTPException, status
from typing import List, Dict, Any, Optional
import oracledb
from database import get_connection
from auth import User

def _check_node_ownership(node_id: int, current_user: User, cursor: oracledb.Cursor):
    """
    SECURITY HELPER: Checks if a user has permission to access a specific node
    by matching their area_id.
    """
    if current_user.role_id in [2, 3]:
        if current_user.area_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your account is not assigned to an area.",
            )

        sql = "SELECT area_id FROM nodes WHERE id = :node_id"
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

def get_data(root_node_id: Optional[int], current_user: User) -> List[Dict[str, Any]]:
    """
    Fetches data from the 'nodes' table, strictly applying area_id authorization
    for both Admins (role_id 2) and Resellers (role_id 3).
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

        auth_clause = " AND n.area_id = :user_area_id"
        auth_clause_connect_by = " AND PRIOR n.area_id = :user_area_id"
        params["user_area_id"] = current_user.area_id

        if root_node_id is not None:
            _check_node_ownership(root_node_id, current_user, cursor)
            params["root_node_id_bv"] = root_node_id
            auth_clause_sub = auth_clause.replace(" n.", " nn.")
            auth_clause_sub_connect_by = auth_clause_connect_by.replace(" n.", " nn.")
            sql = f"""
                SELECT n.* FROM nodes n WHERE 1=1 {auth_clause} START WITH n.id = :root_node_id_bv CONNECT BY PRIOR n.id = n.parent_id {auth_clause_connect_by}
                UNION
                SELECT n.* FROM nodes n WHERE n.id IN (
                    SELECT nn.id FROM nodes nn START WITH nn.parent_id IS NULL AND nn.sw_id = :root_node_id_bv {auth_clause_sub} CONNECT BY PRIOR nn.id = nn.parent_id {auth_clause_sub_connect_by}
                ) {auth_clause}
            """
        else:
            sql = f"""
                SELECT n.* FROM nodes n
                WHERE (n.node_type NOT IN ('PON', 'ONU') AND (n.parent_id IS NULL OR n.parent_id NOT IN (
                    SELECT id FROM nodes WHERE node_type IN ('OLT', 'PON', 'ONU') AND sw_id IS NOT NULL
                )) AND n.sw_id IS NULL)
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
