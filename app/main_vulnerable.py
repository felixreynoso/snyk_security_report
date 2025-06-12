from fastapi import FastAPI, Depends, HTTPException, Request

from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import uvicorn
import sqlite3

import requests

import os

import subprocess

import yaml

from typing import Optional

app = FastAPI()

security = HTTPBearer()

# VULNERABILITY 1: Hardcoded secrets

DATABASE_PASSWORD = "super_secret_password_123"

API_KEY = "sk-1234567890abcdef"

JWT_SECRET = "my-super-secret-jwt-key"

# VULNERABILITY 2: Hardcoded database credentials

DATABASE_URL = "postgresql://admin:password123@localhost:5432/mydb"


@app.get("/")
async def root():
    return {"message": "Hello World"}


# VULNERABILITY 3: SQL Injection - No authentication + Raw SQL


@app.get("/users/{user_id}")
async def get_user(user_id: str):
    conn = sqlite3.connect("users.db")

    cursor = conn.cursor()

    # Direct string interpolation - SQL injection vulnerability

    query = f"SELECT * FROM users WHERE id = '{user_id}'"

    cursor.execute(query)

    result = cursor.fetchone()

    conn.close()

    return {"user": result}


# VULNERABILITY 4: No authentication on sensitive endpoint


@app.get("/admin/users")
async def get_all_users():
    conn = sqlite3.connect("users.db")

    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users")

    users = cursor.fetchall()

    conn.close()

    return {"users": users}


# VULNERABILITY 5: Command injection


@app.post("/execute")
async def execute_command(command: str):
    # Direct command execution without sanitization

    result = subprocess.run(command, shell=True, capture_output=True, text=True)

    return {"output": result.stdout, "error": result.stderr}


# VULNERABILITY 6: Insecure deserialization
@app.post("/load-config")
async def load_config(config_data: str):
    # Using unsafe yaml.load instead of yaml.safe_load

    config = yaml.load(config_data, Loader=yaml.Loader)

    return {"config": config}


# VULNERABILITY 7: SSRF (Server-Side Request Forgery)


@app.get("/fetch-url")
async def fetch_url(url: str):
    # No URL validation - allows internal network access

    response = requests.get(url)

    return {"content": response.text}


# VULNERABILITY 8: Weak authentication


@app.get("/protected")
async def protected_endpoint(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    # Weak token validation
    if credentials.credentials == "admin":
        return {"message": "Access granted", "secret": API_KEY}

    raise HTTPException(status_code=401, detail="Unauthorized")


# VULNERABILITY 9: Information disclosure
@app.get("/debug")
async def debug_info():
    return {
        "environment_vars": dict(os.environ),
        "database_password": DATABASE_PASSWORD,
        "api_key": API_KEY,
    }


# VULNERABILITY 10: Path traversal


@app.get("/files/{filename}")
async def get_file(filename: str):
    # No path validation - allows directory traversal
    file_path = f"./files/{filename}"

    try:
        with open(file_path, "r") as file:
            content = file.read()
            return {"content": content}

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
