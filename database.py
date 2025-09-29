# database.py
import os
import oracledb
from dotenv import load_dotenv

# Load environment variables from the .env file
load_dotenv()


# --- 1. Initialize the Oracle Instant Client (if path is provided) ---
CLIENT_LOC = os.getenv("INSTANT_CLIENT_LOC")
if CLIENT_LOC:
    try:
        # This must be called before any connection is made
        oracledb.init_oracle_client(lib_dir=CLIENT_LOC)
        print(f"✅ Oracle Instant Client initialized from: {CLIENT_LOC}")
    except oracledb.Error as e:
        print(f"❌ Error initializing Oracle Client: {e}")
        # Depending on your setup, you might want to exit if the client fails to load
        # exit(1)


# --- 2. Get Oracle Credentials from Environment Variables ---
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_SID = os.getenv("DB_SID")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")

# Check if all required environment variables are set
if not all([DB_HOST, DB_PORT, DB_SID, DB_USER, DB_PASS]):
    raise ValueError("Missing one or more required database environment variables.")


# --- 3. Construct the DSN (Data Source Name) string ---
DB_DSN = f"{DB_HOST}:{DB_PORT}/{DB_SID}"


def get_connection():
    """
    Creates and returns a new connection to the Oracle database.
    Raises an exception if the connection fails.
    """
    try:
        conn = oracledb.connect(user=DB_USER, password=DB_PASS, dsn=DB_DSN)
        return conn
    except oracledb.Error as e:
        print(f"❌ Error while connecting to Oracle: {e}")
        # Re-raise the exception so the API endpoint can handle it
        raise