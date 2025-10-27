import os
import oracledb
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# --- 1. Initialize Oracle Client (Unchanged) ---
CLIENT_LOC = os.getenv("INSTANT_CLIENT_LOC")
if CLIENT_LOC:
    try:
        oracledb.init_oracle_client(lib_dir=CLIENT_LOC)
        print(f"✅ Oracle Instant Client initialized from: {CLIENT_LOC}")
    except oracledb.Error as e:
        print(f"❌ Error initializing Oracle Client: {e}")
        # exit(1) # Consider exiting if this fails

# --- 2. Get Credentials (Unchanged) ---
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_SID = os.getenv("DB_SID")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")

if not all([DB_HOST, DB_PORT, DB_SID, DB_USER, DB_PASS]):
    raise ValueError("Missing one or more required database environment variables.")

# --- 3. Construct DSN (Unchanged) ---
DB_DSN = f"{DB_HOST}:{DB_PORT}/{DB_SID}"

# --- 4. (NEW) Create the Connection Pool ---
# This pool is created once when the module is imported (on app startup)
try:
    pool = oracledb.create_pool(
        user=DB_USER,
        password=DB_PASS,
        dsn=DB_DSN,
        min=4,  # Start with 4 connections
        max=10,  # Allow up to 10 connections
        increment=1,  # Add 1 connection at a time when needed
    )
    print("✅ Oracle Connection Pool created successfully.")
except oracledb.Error as e:
    print(f"❌ Error creating Oracle Connection Pool: {e}")
    # This is a critical failure, the app can't run.
    raise


# --- 5. (UPDATED) get_connection Function ---
def get_connection():
    """
    Acquires and returns a connection from the pool.
    Raises an exception if the connection fails.
    """
    try:
        # pool.acquire() is extremely fast compared to oracledb.connect()
        conn = pool.acquire()
        return conn
    except oracledb.Error as e:
        print(f"❌ Error acquiring connection from pool: {e}")
        # Re-raise so the API endpoint can handle it
        raise
