# app/routers/demo_router.py
import io
import logging
import zipfile
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from fastapi_azure_auth.user import User

from app.db import blob_service_client
from app.helpers import track_user_login

from app.db import users_collection
from ..azure_scheme import azure_scheme
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

router = APIRouter(tags=["Demo"])
logging.basicConfig(level=logging.INFO)


@router.get("/demo_viewer", dependencies=[Security(azure_scheme)])
async def demo_get_viewer_data(user: User = Security(azure_scheme)):
    await track_user_login(users_collection, user)
    try:
        job = {
            "_id": "demo",
            "fileName": "XYZ1234.WCL",
            "status": "COMPLETE",
            "created_at": {"$date": "2025-04-29T17:26:47.574Z"},
            "file_configs": {
                "holeid": "XYZ1234",
                "amplitude_log": "Amplitude-HS",
                "traveltime_log": "TravelTime-HS",
                "image_log": "None",
                "azimuth_log": "Azimuth",
                "tilt_log": "Tilt",
                "orientation_type": "high_side",
            },
            "hole_diameter": 96,
            "units": "metric",
            "declination": 0,
            "user": "FReynoso@bgcengineering.ca",
            "inputUrl": "https://galileofiles.blob.core.windows.net/galileofiles/input/ef482c50-2eaa-4006-85ac-2e545d03981d/XYZ1234.WCL",
            "outputUrl": "https://galileofiles.blob.core.windows.net/galileofiles/demo/holeid",
            "completed_at": {"$date": "2025-04-29T17:30:58.878Z"},
            "warnings": [],
        }

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

        # Look for depth range file
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

        # Look for fracture/structure data
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


@router.get("/demo_image", dependencies=[Security(azure_scheme)])
async def demo_get_output_file(user: User = Security(azure_scheme)):
    await track_user_login(users_collection, user)
    output_url = "https://galileofiles.blob.core.windows.net/galileofiles/demo/holeid"
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


@router.get("/demo_quality", dependencies=[Security(azure_scheme)])
async def demo_get_quality_file(user: User = Security(azure_scheme)):
    output_url = "https://galileofiles.blob.core.windows.net/galileofiles/demo/holeid"
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


@router.get("/demo_export_zip", dependencies=[Security(azure_scheme)])
async def demo_export_files_as_zip(user: User = Security(azure_scheme)):
    await track_user_login(users_collection, user)
    output_url = "https://galileofiles.blob.core.windows.net/galileofiles/demo/holeid"

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
    filename = "demo_job_XYZ1234.zip"

    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
