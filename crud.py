from fastapi import FastAPI, HTTPException
import oracledb
from database import get_connection

def get_data(sw_id: int = None):
    conn = None
    try:
        conn = get_connection()
        print("✅ Connection successful!")
        cursor = conn.cursor()

        # 1. Start with the base query without the WHERE clause
        sql = """
            SELECT ID, NAME, NVL(NODE_TYPE, null), NVL(LINK_TYPE, null), 
                   PARENT_ID, STATUS, SW_ID, NVL(VLAN, null), NVL(CABLE_COLOR, 'black'), NVL(CABLE_DESC, null), NVL(LAT1, null), NVL(LONG1, null),
                   NVL(SERIAL_NO, null), NVL(BRAND, null), NVL(MAC, null), 
                   NVL(MODEL, null), NVL(REMARKS, null) 
            FROM nodes
        """
        
        params = {} # Create an empty dictionary for bind parameters

        # 2. Dynamically add the WHERE clause only if sw_id is provided
        if sw_id is not None:
            sql += " WHERE SW_ID = :sw_id_bv"
            params["sw_id_bv"] = sw_id
            print(f"Executing query for SW_ID: {sw_id}")
        else:
            print("Executing query for all SW_IDs")

        # 3. Execute the query with the (possibly empty) params
        cursor.execute(sql, params)

        result = cursor.fetchall()
        cursor.close()

        if result:
            data = []
            for res in result:
                dataObject = {
                    "id": res[0], "name": res[1], "node_type": res[2], "link_type": res[3],
                    "parent_id": res[4], "status": res[5], "sw_id": res[6], "vlan": res[7],
                    "cable_color": res[8], "cable_desc": res[9], "lat1": res[10], "long1": res[11],
                    "serial_no": res[12], "brand": res[13], "mac": res[14],
                    "model": res[15], "remarks": res[16],
                }
                data.append(dataObject)
            return data
        else:
            # Return an empty list, which is not an error
            return []

    except oracledb.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")
    finally:
        if conn:
            conn.close()
            print("Connection closed.")