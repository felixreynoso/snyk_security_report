# app/config.py
import os
from typing import List
from pydantic import AnyHttpUrl, computed_field
from pydantic_settings import BaseSettings
from azure.identity import ClientSecretCredential, DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

# Environment variables
client_id = os.getenv("AZURE_CLIENT_ID")
tenant_id = os.getenv("AZURE_TENANT_ID")
client_secret = os.getenv("AZURE_CLIENT_SECRET")

# Create credential based on available info
if client_id and tenant_id and client_secret:
    print(f"Using ClientSecretCredential with client_id: {client_id[:5]}...")
    credential = ClientSecretCredential(
        tenant_id=tenant_id, client_id=client_id, client_secret=client_secret
    )
else:
    print("Using DefaultAzureCredential")
    credential = DefaultAzureCredential()

# Access Azure Key Vault
key_vault_url = "https://galileokey.vault.azure.net/"
secret_client = SecretClient(vault_url=key_vault_url, credential=credential)


class Settings(BaseSettings):
    BACKEND_CORS_ORIGINS: list[str | AnyHttpUrl] = [
        "http://localhost:8000",
        "https://localhost:44406",
        "https://beta.galileo.bgcengineering.ca",
        "https://structura.bgcengineering.ca",
        "https://beta.structura.bgcengineering.ca",
        "https://galileo-webapp.gentlesmoke-c70a7869.canadacentral.azurecontainerapps.io",
    ]
    OPENAPI_CLIENT_ID: str | None = None
    APP_CLIENT_ID: str | None = None
    TENANT_ID: str | None = None
    SCOPE_DESCRIPTION: str | None = None
    ENV: str = os.getenv("AZURE_CLIENT_ID", "development")

    # Whitelist configuration
    ALLOWED_ORIGINS: List[str] = [
        "localhost",
        "127.0.0.1",
        "galileo-webapp.gentlesmoke-c70a7869.canadacentral.azurecontainerapps.io",
        "beta.galileo.bgcengineering.ca",
        "structura.bgcengineering.ca",
        "beta.structura.bgcengineering.ca",
    ]

    ALLOWED_IPS: List[str] = [
        "127.0.0.1",
        "::1",  # IPv6 localhost
        "20.175.244.43",
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.OPENAPI_CLIENT_ID = secret_client.get_secret("OPENAPICLIENTID").value
        self.APP_CLIENT_ID = secret_client.get_secret("APPCLIENTID").value
        self.TENANT_ID = secret_client.get_secret("GALILEOAPITENANTID").value
        self.SCOPE_DESCRIPTION = secret_client.get_secret("SCOPEDESCRIPTION").value

    @computed_field
    @property
    def SCOPE_NAME(self) -> str:
        return f"api://{self.APP_CLIENT_ID}/{self.SCOPE_DESCRIPTION}"

    @computed_field
    @property
    def SCOPES(self) -> dict:
        return {
            self.SCOPE_NAME: self.SCOPE_DESCRIPTION,
        }

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "allow"


settings = Settings()
