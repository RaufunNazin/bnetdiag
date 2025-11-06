import os
import oracledb
from dotenv import load_dotenv

load_dotenv()

CLIENT_LOC = os.getenv("INSTANT_CLIENT_LOC")
if CLIENT_LOC:
    try:
        oracledb.init_oracle_client(lib_dir=CLIENT_LOC)
        print(f"✅ Oracle Instant Client initialized from: {CLIENT_LOC}")
    except oracledb.Error as e:
        print(f"❌ Error initializing Oracle Client: {e}")

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_SID = os.getenv("DB_SID")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")

if not all([DB_HOST, DB_PORT, DB_SID, DB_USER, DB_PASS]):
    raise ValueError("Missing one or more required database environment variables.")

DB_DSN = f"{DB_HOST}:{DB_PORT}/{DB_SID}"

try:
    pool = oracledb.create_pool(
        user=DB_USER,
        password=DB_PASS,
        dsn=DB_DSN,
        min=4,
        max=10,
        increment=1,
    )
    print("✅ Oracle Connection Pool created successfully.")
except oracledb.Error as e:
    print(f"❌ Error creating Oracle Connection Pool: {e}")
    raise


def get_connection():
    """
    Acquires and returns a connection from the pool.
    Raises an exception if the connection fails.
    """
    try:
        conn = pool.acquire()
        return conn
    except oracledb.Error as e:
        print(f"❌ Error acquiring connection from pool: {e}")
        raise
