# app/routers/main_router.py
import asyncio
import base64
import json
import logging
import os
import tempfile
import uuid
import io
import zipfile
from datetime import datetime
from pathlib import Path
from typing import List
from urllib.parse import urlparse
import re

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Security,
    UploadFile,
    Response,
)
from fastapi.responses import JSONResponse
from fastapi_azure_auth.user import User
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from ..azure_scheme import azure_scheme

from app.db import (
    jobs_collection,
    users_collection,
    blob_service_client,
    BLOB_CONTAINER_NAME,
    SERVICEBUS_CONN_STRING,
    SERVICEBUS_QUEUE_NAME,
)
from app.helpers import track_user_login

logging.basicConfig(level=logging.INFO)

router = APIRouter(tags=["Standard"])


@router.get("/test", dependencies=[Security(azure_scheme)])
async def read_user(user: User = Security(azure_scheme)):
    await track_user_login(users_collection, user)
    testVar = os.getenv("testVar", "not found")
    return {"user_id": 1, "name": "Galileo", "testVar": testVar}


@router.get("/jobs", dependencies=[Security(azure_scheme)])
async def get_jobs(user: User = Security(azure_scheme)):
    await track_user_login(users_collection, user)
    try:
        jobs = list(
            jobs_collection.find(
                {"user": user.preferred_username},
                {
                    "_id": 1,
                    "fileName": 1,
                    "status": 1,
                    "outputUrl": 1,
                    "error": 1,
                    "created_at": 1,
                },
            )
        )
        for job in jobs:
            job["id"] = str(job.pop("_id"))
        return jobs
    except Exception as e:
        logging.error(f"Error fetching jobs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str, user: User = Security(azure_scheme)):
    await track_user_login(users_collection, user)
    try:
        job = jobs_collection.find_one({"_id": job_id, "user": user.preferred_username})
        if not job:
            raise HTTPException(
                status_code=404, detail="Job not found or access denied"
            )

        # Delete associated blobs
        if "inputUrl" in job:
            try:
                input_path = urlparse(job["inputUrl"]).path.lstrip("/")
                container_name, blob_path = input_path.split("/", 1)
                blob_client = blob_service_client.get_blob_client(
                    container=BLOB_CONTAINER_NAME, blob=blob_path
                )
                blob_client.delete_blob()
                logging.info(f"Deleted input blob for job {job_id}")
            except Exception as e:
                logging.warning(
                    f"Could not delete input blob for job {job_id}: {str(e)}"
                )

        if "outputUrl" in job:
            try:
                output_path = urlparse(job["outputUrl"]).path.lstrip("/")
                container_name, output_prefix = output_path.split("/", 1)
                container_client = blob_service_client.get_container_client(
                    BLOB_CONTAINER_NAME
                )
                blobs_to_delete = container_client.list_blobs(
                    name_starts_with=output_prefix
                )
                for blob in blobs_to_delete:
                    blob_client = blob_service_client.get_blob_client(
                        container=BLOB_CONTAINER_NAME, blob=blob.name
                    )
                    blob_client.delete_blob()
                logging.info(f"Deleted output blobs for job {job_id}")
            except Exception as e:
                logging.warning(
                    f"Could not delete output blobs for job {job_id}: {str(e)}"
                )

        result = jobs_collection.delete_one(
            {"_id": job_id, "user": user.preferred_username}
        )
        if result.deleted_count == 0:
            raise HTTPException(
                status_code=404, detail="Job not found or already deleted"
            )

        logging.info(f"User {user.preferred_username} deleted job {job_id}")
        return {"message": "Job and associated data deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error deleting job {job_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to delete job: {str(e)}")


@router.post("/upload")
async def upload_files(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    file_configs: str = Form("{}"),
    process_breakouts_flag: bool = True,
    hole_diameter: float = Form(96.0),
    units: str = Form("metric"),
    declination: float = Form(0.0),
    user: User = Security(azure_scheme),
):
    await track_user_login(users_collection, user)
    created_jobs = []

    try:
        for file in files:
            if not file.filename.endswith(".WCL"):
                continue

            job_id = str(uuid.uuid4())
            temp_file_path = Path(tempfile.gettempdir()) / f"{job_id}_{file.filename}"

            with open(temp_file_path, "wb") as temp_file:
                while chunk := await file.read(1024 * 1024):  # 1MB chunks
                    temp_file.write(chunk)

            job = {
                "_id": job_id,
                "fileName": file.filename,
                "status": "CREATED",
                "created_at": datetime.utcnow(),
                "file_configs": json.loads(file_configs).get(file.filename, {}),
                "process_breakouts_flag": process_breakouts_flag,
                "hole_diameter": hole_diameter,
                "units": units,
                "declination": declination,
                "user": user.preferred_username,
            }

            jobs_collection.insert_one(job)
            background_tasks.add_task(
                process_file_upload,
                str(temp_file_path),
                file.filename,
                job_id,
                blob_service_client,
                BLOB_CONTAINER_NAME,
                jobs_collection,
            )

            created_jobs.append(
                {"id": job_id, "fileName": file.filename, "status": "UPLOADING"}
            )

        return {
            "message": f"Processing {len(created_jobs)} files",
            "jobs": created_jobs,
        }

    except Exception as e:
        logging.error(f"Error in upload: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# Helper functions
async def process_file_upload(
    temp_file_path: str,
    original_filename: str,
    job_id: str,
    blob_service_client,
    container_name: str,
    jobs_collection,
):
    """Process file upload in background using a temporary file"""
    try:
        sanitized_filename = re.sub(r"[^a-zA-Z0-9._-]", "", original_filename)

        # Ensure we don't end up with an empty filename
        if not sanitized_filename:
            sanitized_filename = "file"

        blob_name = f"input/{job_id}/{sanitized_filename}"
        blob_client = blob_service_client.get_blob_client(
            container=container_name, blob=blob_name
        )

        chunk_size = 1024 * 1024  # 1MB chunks
        block_list = []
        block_num = 0

        with open(temp_file_path, "rb") as file:
            while True:
                chunk = file.read(chunk_size)
                if not chunk:
                    break

                block_id = base64.b64encode(f"block-{block_num:08d}".encode()).decode()
                await asyncio.to_thread(
                    blob_client.stage_block, block_id=block_id, data=chunk
                )
                block_list.append(block_id)
                block_num += 1

        if block_list:
            await asyncio.to_thread(blob_client.commit_block_list, block_list)
        else:
            await asyncio.to_thread(blob_client.upload_blob, data=b"", overwrite=True)

        input_url = blob_client.url
        jobs_collection.update_one(
            {"_id": job_id}, {"$set": {"status": "CREATED", "inputUrl": input_url}}
        )

        await send_to_queue(job_id)

        try:
            os.unlink(temp_file_path)
        except Exception as e:
            logging.warning(
                f"Failed to delete temporary file {temp_file_path}: {str(e)}"
            )

    except Exception as e:
        logging.error(
            f"Error uploading file {original_filename} for job {job_id}: {str(e)}"
        )
        jobs_collection.update_one(
            {"_id": job_id},
            {"$set": {"status": "FAILED", "error": f"Upload failed: {str(e)}"}},
        )
        try:
            os.unlink(temp_file_path)
        except Exception as cleanup_error:
            logging.warning(
                f"Failed to delete temporary file {temp_file_path}: {str(cleanup_error)}"
            )


async def send_to_queue(job_id):
    try:
        message = {"jobId": job_id}
        servicebus_client = ServiceBusClient.from_connection_string(
            SERVICEBUS_CONN_STRING
        )

        with servicebus_client:
            sender = servicebus_client.get_queue_sender(
                queue_name=SERVICEBUS_QUEUE_NAME
            )
            with sender:
                message = ServiceBusMessage(json.dumps(message))
                sender.send_messages(message)
                logging.info(f"Job {job_id} sent to queue")

    except Exception as e:
        jobs_collection.update_one(
            {"_id": job_id}, {"$set": {"status": "FAILED", "error": str(e)}}
        )
        logging.error(f"Error sending job to queue: {str(e)}")


@router.get("/viewer/{jobid}", dependencies=[Security(azure_scheme)])
async def get_viewer_data(jobid: str, user: User = Security(azure_scheme)):
    await track_user_login(users_collection, user)
    try:
        job = jobs_collection.find_one({"_id": jobid, "user": user.preferred_username})
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.get("status") != "COMPLETE":
            raise HTTPException(
                status_code=400,
                detail=f"Job status is {job.get('status')}, not COMPLETE",
            )

        output_url = job.get("outputUrl")
        if not output_url:
            raise HTTPException(
                status_code=404, detail="No output URL found for this job"
            )

        response_data = {
            "job": {
                "id": str(job["_id"]),
                "fileName": job["fileName"],
                "holeid": job.get("file_configs", {}).get("holeid", "Unknown"),
                "logType": "ATV",
                "units": job.get("units", "metric"),
            },
            "depthRange": {"min": 0, "max": 100},
            "stereonet": {"available": False, "data": None, "dataType": None},
            "warnings": job.get("warnings", []),
        }

        output_path = urlparse(output_url).path.lstrip("/")
        container_name, folder_path = output_path.split("/", 1)
        container_client = blob_service_client.get_container_client(container_name)
        blobs = list(container_client.list_blobs(name_starts_with=folder_path))

        # Process depth range
        depth_range_file = next(
            (b for b in blobs if b.name.endswith("_depth_range.txt")), None
        )
        if depth_range_file:
            blob_client = container_client.get_blob_client(depth_range_file)
            depth_range_content = blob_client.download_blob().readall().decode("utf-8")
            try:
                depth_ranges = depth_range_content.strip().split("\n")
                depth_ranges = [float(nbr[:-2]) for nbr in depth_ranges]
                min_depth, max_depth = depth_ranges
                response_data["depthRange"] = {"min": min_depth, "max": max_depth}
            except Exception as e:
                logging.error(f"Error parsing depth range data: {str(e)}")

        # Process fracture/structure data
        fracture_data_file = next(
            (b for b in blobs if "_fracture_data.csv" in b.name), None
        )
        if fracture_data_file:
            blob_client = container_client.get_blob_client(fracture_data_file)
            csv_content = blob_client.download_blob().readall().decode("utf-8")
            response_data["stereonet"] = {
                "available": True,
                "data": csv_content,
                "dataType": "fracture",
            }
        else:
            structure_data_file = next(
                (b for b in blobs if "_structure_data.csv" in b.name), None
            )
            if structure_data_file:
                blob_client = container_client.get_blob_client(structure_data_file)
                csv_content = blob_client.download_blob().readall().decode("utf-8")
                response_data["stereonet"] = {
                    "available": True,
                    "data": csv_content,
                    "dataType": "structure",
                }

        return response_data

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error fetching viewer data: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/image/{jobid}", dependencies=[Security(azure_scheme)])
async def get_output_file(jobid: str, user: User = Security(azure_scheme)):
    await track_user_login(users_collection, user)
    job = jobs_collection.find_one({"_id": jobid, "user": user.preferred_username})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    output_url = job.get("outputUrl")
    if not output_url:
        raise HTTPException(status_code=404, detail="No output URL found for this job")

    output_path = urlparse(output_url).path.lstrip("/")
    container_name, folder_path = output_path.split("/", 1)
    container_client = blob_service_client.get_container_client(container_name)
    blobs = list(container_client.list_blobs(name_starts_with=folder_path))

    matching_file = next(
        (b for b in blobs if "_stitched_with_" in b.name and b.name.endswith(".png")),
        None,
    )

    if matching_file:
        blob_client = container_client.get_blob_client(matching_file)
        image_content = blob_client.download_blob().readall()
        return Response(content=image_content, media_type="image/png")
    else:
        raise HTTPException(status_code=404, detail="No matching image found")


@router.get("/quality/{jobid}", dependencies=[Security(azure_scheme)])
async def get_quality_file(jobid: str, user: User = Security(azure_scheme)):
    await track_user_login(users_collection, user)
    job = jobs_collection.find_one({"_id": jobid, "user": user.preferred_username})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    output_url = job.get("outputUrl")
    output_path = urlparse(output_url).path.lstrip("/")
    container_name, folder_path = output_path.split("/", 1)
    container_client = blob_service_client.get_container_client(container_name)
    blobs = list(container_client.list_blobs(name_starts_with=folder_path))

    matching_file = next(
        (b for b in blobs if "_image_quality" in b.name and b.name.endswith(".csv")),
        None,
    )

    if matching_file:
        blob_client = container_client.get_blob_client(matching_file)
        quality_content = blob_client.download_blob().readall()
        csv_content = quality_content.decode("utf-8").strip().split("\n")
        headers = csv_content[0][:-1].split(",")
        rows = csv_content[1:]

        data = [
            {
                headers[i]: (
                    round(float(value), 3)
                    if value.replace(".", "", 2).isdigit()
                    else value
                )
                for i, value in enumerate(row[:-1].split(","))
            }
            for row in rows
        ]

        return JSONResponse(content=data)
    else:
        raise HTTPException(status_code=404, detail="No matching image found")


@router.get("/export_zip/{jobid}", dependencies=[Security(azure_scheme)])
async def export_files_as_zip(jobid: str, user: User = Security(azure_scheme)):
    await track_user_login(users_collection, user)
    job = jobs_collection.find_one({"_id": jobid, "user": user.preferred_username})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    output_url = job.get("outputUrl")
    if not output_url:
        raise HTTPException(status_code=404, detail="No output URL found for this job")

    output_path = urlparse(output_url).path.lstrip("/")
    container_name, folder_path = output_path.split("/", 1)
    container_client = blob_service_client.get_container_client(container_name)
    blobs = list(container_client.list_blobs(name_starts_with=folder_path))

    if not blobs:
        raise HTTPException(
            status_code=404, detail="No files found in the output location"
        )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for blob in blobs:
            blob_client = container_client.get_blob_client(blob.name)
            file_content = blob_client.download_blob().readall()
            filename = blob.name.split("/")[-1]
            zip_file.writestr(filename, file_content)

    zip_buffer.seek(0)
    job_name = job.get("file_configs", {}).get("holeid", "output")
    safe_job_name = "".join(
        c if c.isalnum() or c in "_- " else "_" for c in job_name
    ).strip()
    filename = f"{safe_job_name}_job_{jobid}.zip"

    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/user/info", dependencies=[Security(azure_scheme)])
async def get_user_info(user: User = Security(azure_scheme)):
    try:
        await track_user_login(users_collection, user)
        user_data = users_collection.find_one({"username": user.preferred_username})

        if not user_data:
            raise HTTPException(status_code=404, detail="User not found")

        response = {
            "username": user_data["username"],
            "email": user_data.get("email"),
            "name": user_data.get("name"),
            "createdDate": user_data["createdDate"].isoformat(),
            "lastLogin": user_data["lastLogin"].isoformat(),
            "quota": user_data["quota"],
            "quotaUsed": len(
                list(jobs_collection.find({"user": user.preferred_username}))
            ),
            "roles": user_data.get("roles", ["user"]),
        }

        return response

    except Exception as e:
        logging.error(f"Error getting user info: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/user/dashboard", dependencies=[Security(azure_scheme)])
async def get_user_dashboard(
    user: User = Security(azure_scheme),
    months: int = 12,
    recent_jobs_limit: int = 10,
):
    """User endpoint to get personal dashboard analytics"""
    await track_user_login(users_collection, user)
    # [Implementation remains the same as in original file]
    # ... (copy the entire implementation from the original file)
