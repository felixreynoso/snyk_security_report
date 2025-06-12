# uvicorn app.main:app --reload
import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import AsyncGenerator
from urllib.parse import urlparse

from fastapi import FastAPI, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi_azure_auth import SingleTenantAzureAuthorizationCodeBearer
from fastapi_azure_auth.user import User
from dotenv import load_dotenv

from app.config import settings
from app.db import jobs_collection, blob_service_client, BLOB_CONTAINER_NAME
from app.routers import main_router, demo_router, admin_router

from app.azure_scheme import azure_scheme
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

scheduler = AsyncIOScheduler()

load_dotenv()
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Load OpenID config on startup and start scheduler."""
    await azure_scheme.openid_config.load_config()

    # Add the cleanup job to run every 12 hours
    scheduler.add_job(
        cleanup_old_jobs,
        trigger=IntervalTrigger(hours=12),
        id="cleanup_old_jobs",
        name="Clean up old jobs and blobs",
        replace_existing=True,
    )

    # Start the scheduler
    scheduler.start()
    logging.info("Background scheduler started")

    try:
        yield
    finally:
        # Shutdown the scheduler
        scheduler.shutdown()
        logging.info("Background scheduler stopped")


# Create FastAPI application
app = FastAPI(
    title="Galileo API - Borehole Image Analysis",
    lifespan=lifespan,
    swagger_ui_oauth2_redirect_url="/oauth2-redirect",
    swagger_ui_init_oauth={
        "usePkceWithAuthorizationCodeGrant": True,
        "clientId": settings.OPENAPI_CLIENT_ID,
    },
)


# Add no-cache headers middleware
@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# Add CORS middleware
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin) for origin in settings.BACKEND_CORS_ORIGINS],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# Create dependency for routes
async def get_current_user(user: User = Security(azure_scheme)):
    return user


# Include routers with authentication dependency
app.include_router(main_router.router)
app.include_router(demo_router.router)
app.include_router(admin_router.router)


# Root endpoint
@app.get("/", tags=["Health"])
def read_root():
    return {"message": "Simple Test App Is Running"}


# Cleanup job
async def cleanup_old_jobs():
    """Periodically check for and delete jobs older than 12 hours along with their associated blob files."""
    try:
        cutoff_time = datetime.utcnow() - timedelta(hours=12)
        query = {"created_at": {"$lt": cutoff_time}}
        jobs_to_delete = list(jobs_collection.find(query))

        if not jobs_to_delete:
            logging.info("No old jobs to clean up")
            return

        logging.info(
            f"Found {len(jobs_to_delete)} jobs older than 12 hours for deletion"
        )

        for job in jobs_to_delete:
            job_id = job["_id"]

            try:
                # Delete input blob if exists
                if "inputUrl" in job:
                    input_path = urlparse(job["inputUrl"]).path.lstrip("/")
                    container_name, blob_path = input_path.split("/", 1)
                    blob_client = blob_service_client.get_blob_client(
                        container=BLOB_CONTAINER_NAME, blob=blob_path
                    )
                    try:
                        blob_client.delete_blob()
                        logging.info(f"Deleted input blob for job {job_id}")
                    except Exception as e:
                        logging.warning(
                            f"Could not delete input blob for job {job_id}: {str(e)}"
                        )

                # Delete output blob if exists
                if "outputUrl" in job:
                    output_path = urlparse(job["outputUrl"]).path.lstrip("/")
                    container_name, blob_path = output_path.split("/", 1)
                    blob_client = blob_service_client.get_blob_client(
                        container=BLOB_CONTAINER_NAME, blob=blob_path
                    )
                    try:
                        blob_client.delete_blob()
                        logging.info(f"Deleted output blob for job {job_id}")
                    except Exception as e:
                        logging.warning(
                            f"Could not delete output blob for job {job_id}: {str(e)}"
                        )

                # Delete job document from database
                jobs_collection.delete_one({"_id": job_id})
                logging.info(f"Deleted job {job_id} from database")

            except Exception as e:
                logging.error(f"Error cleaning up job {job_id}: {str(e)}")

        logging.info(f"Cleanup completed - deleted {len(jobs_to_delete)} old jobs")

    except Exception as e:
        logging.error(f"Error in cleanup_old_jobs: {str(e)}")


# Running the app
if __name__ == "__main__":
    import uvicorn

    if os.environ.get("ENV") == "development":
        port = 5000
        uvicorn.run("app.main:app", host="localhost", port=port, reload=True)
    else:
        port = int(os.environ.get("PORT", 80))
        uvicorn.run("app.main:app", host="0.0.0.0", port=port)
