import oracledb
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta
from passlib.context import CryptContext
from database import get_connection
import os  # <-- 1. Import the os module
from dotenv import load_dotenv

load_dotenv()

# --- Configuration ---
# ⚠️ IMPORTANT: Generate a real, random secret key!
# Run this in your terminal to get one:
# python -c 'import secrets; print(secrets.token_hex(32))'
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60*24*5  # 5 days

if not SECRET_KEY:
    raise ValueError("No SECRET_KEY set for JWT. Please create a .env file.")


# --- Password Hashing ---
# This context performs a simple plaintext comparison, as requested.
class PlainTextContext:
    def verify(self, secret, hash):
        return secret == hash

    def hash(self, secret):
        return secret


# ⚠️ SECURITY NOTE: Storing plaintext passwords is a major vulnerability.
# You should migrate to storing hashed passwords (e.g., using bcrypt).
pwd_context = PlainTextContext()


# --- Pydantic Models ---


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    username: Optional[str] = None


class User(BaseModel):
    id: int
    username: str
    role_id: int
    area_id: Optional[int] = None
    first_name: Optional[str] = None


# --- OAuth2 Scheme ---
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


# --- Utility Functions ---


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def get_user_from_db(username: str) -> Optional[User]:
    """
    Fetches user details from the database.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        sql = "SELECT id, username, role_id, area_id, first_name FROM oms_users WHERE username = :username"

        cursor.execute(sql, {"username": username})
        row = cursor.fetchone()

        if row:
            return User(
                id=row[0],
                username=row[1],
                role_id=row[2],
                area_id=row[3],
                first_name=row[4],
            )
        return None
    except oracledb.Error as e:
        print(f"Error fetching user: {e}")
        return None
    finally:
        if conn:
            conn.close()


def get_user_password_from_db(username: str) -> Optional[str]:
    """
    Fetches ONLY the user's password for verification.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # --- THIS IS THE FIX ---
        # Changed 'pass' to 'PASS' to match your database column name.
        sql = "SELECT PASS FROM oms_users WHERE username = :username"

        cursor.execute(sql, {"username": username})
        row = cursor.fetchone()

        if row:
            return row[0]
        return None
    except oracledb.Error as e:
        print(f"Error fetching user password: {e}")
        return None
    finally:
        if conn:
            conn.close()


# --- Dependency ---


async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    """
    This is the core authorization dependency.
    It verifies the token and returns the current user's data.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError:
        raise credentials_exception

    user = get_user_from_db(token_data.username)
    if user is None:
        raise credentials_exception

    return user


# --- Admin/Reseller Role Checks (Optional but recommended) ---


async def get_current_admin_user(current_user: User = Depends(get_current_user)):
    if current_user.role_id != 2:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation not permitted: Requires admin privileges.",
        )
    return current_user


async def get_current_reseller_user(current_user: User = Depends(get_current_user)):
    if current_user.role_id != 3:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation not permitted: Requires reseller privileges.",
        )
    return current_user
