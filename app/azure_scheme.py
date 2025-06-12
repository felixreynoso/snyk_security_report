# Create Azure authentication scheme
import os
import logging
from contextlib import asynccontextmanager

from typing import AsyncGenerator


from fastapi import FastAPI, Security

from fastapi_azure_auth import SingleTenantAzureAuthorizationCodeBearer


from app.config import settings


azure_scheme = SingleTenantAzureAuthorizationCodeBearer(
    app_client_id=settings.APP_CLIENT_ID,
    tenant_id=settings.TENANT_ID,
    scopes=settings.SCOPES,
    allow_guest_users=True,
)
