# app/routers/admin_router.py
import calendar
import logging
import traceback
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, List, Optional, Union

from fastapi_azure_auth.user import User
from pymongo import ASCENDING, DESCENDING

from app.db import users_collection, jobs_collection, job_analysis_collection, db
from app.helpers import track_user_login
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

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/admin", tags=["Admin"])


@router.get("/users", dependencies=[Security(azure_scheme)])
async def get_admin_users(
    user: User = Security(azure_scheme),
    limit: int = Query(10, ge=1, le=100, description="Number of users to return"),
    skip: int = Query(0, ge=0, description="Number of users to skip for pagination"),
    username_filter: Optional[str] = Query(
        None, description="Filter users by username (partial match)"
    ),
    sort_by: Optional[str] = Query("lastLogin", description="Field to sort by"),
    sort_order: Optional[str] = Query("desc", description="Sort order: asc or desc"),
):
    """Admin endpoint to get users with aggregated job analysis statistics"""
    await track_user_login(users_collection, user)
    db_user = users_collection.find_one({"username": user.preferred_username})

    if not db_user or "admin" not in db_user["roles"]:
        raise HTTPException(status_code=403, detail="Admin access required")

    try:
        user_query = {}
        if username_filter:
            user_query["username"] = {"$regex": f"^{username_filter}", "$options": "i"}

        total_users = users_collection.count_documents(user_query)
        sort_direction = DESCENDING if sort_order.lower() == "desc" else ASCENDING

        sort_field_mapping = {
            "name": "name",
            "username": "username",
            "createdDate": "createdDate",
            "lastLogin": "lastLogin",
            "quota": "quota",
        }

        mongo_sort_field = sort_field_mapping.get(sort_by, "lastLogin")

        users_cursor = (
            users_collection.find(user_query)
            .sort(mongo_sort_field, sort_direction)
            .skip(skip)
            .limit(limit)
        )

        users = list(users_cursor)
        usernames = [user["username"] for user in users]

        # Aggregate job statistics
        job_stats_pipeline = [
            {
                "$match": {
                    "user": {"$in": usernames},
                    "completed_at": {"$exists": True},
                }
            },
            {
                "$group": {
                    "_id": "$user",
                    "total_analyses": {"$sum": 1},
                    "total_processing_time_seconds": {
                        "$sum": "$processing_time_seconds"
                    },
                    "total_file_size_bytes": {"$sum": "$file_size_bytes"},
                    "total_length_processed": {"$sum": "$total_length_processed"},
                    "total_structures_detected": {"$sum": "$total_structures_detected"},
                    "total_fractures": {"$sum": {"$ifNull": ["$total_fractures", 0]}},
                    "total_breakouts": {"$sum": {"$ifNull": ["$total_breakouts", 0]}},
                    "last_analysis_date": {"$max": "$completed_at"},
                }
            },
        ]

        job_stats_cursor = job_analysis_collection.aggregate(job_stats_pipeline)
        job_stats_dict = {stat["_id"]: stat for stat in job_stats_cursor}

        result_users = []
        for user_doc in users:
            username = user_doc["username"]
            user_stats = job_stats_dict.get(
                username,
                {
                    "total_analyses": 0,
                    "total_processing_time_seconds": 0,
                    "total_file_size_bytes": 0,
                    "total_length_processed": 0,
                    "total_structures_detected": 0,
                    "total_fractures": 0,
                    "total_breakouts": 0,
                    "last_analysis_date": None,
                },
            )

            formatted_user = {
                "id": str(user_doc["_id"]),
                "username": user_doc["username"],
                "name": user_doc.get("name", ""),
                "email": user_doc.get("email"),
                "createdDate": user_doc.get("createdDate"),
                "lastLogin": user_doc.get("lastLogin"),
                "quota": user_doc.get("quota", 0),
                "roles": user_doc.get("roles", []),
                "status": user_doc.get("status", "UNKNOWN"),
                "stats": {
                    "total_analyses": user_stats.get("total_analyses", 0),
                    "processing_time_seconds": user_stats.get(
                        "total_processing_time_seconds", 0
                    ),
                    "file_size_bytes": user_stats.get("total_file_size_bytes", 0),
                    "total_length_processed": user_stats.get(
                        "total_length_processed", 0
                    ),
                    "total_structures_detected": user_stats.get(
                        "total_structures_detected", 0
                    ),
                    "total_fractures": user_stats.get("total_fractures", 0),
                    "total_breakouts": user_stats.get("total_breakouts", 0),
                    "last_analysis_date": user_stats.get("last_analysis_date"),
                },
            }

            result_users.append(formatted_user)

        if sort_by.startswith("stats."):
            stat_field = sort_by.split(".", 1)[1]
            reverse_sort = sort_order.lower() == "desc"
            result_users.sort(
                key=lambda x: x["stats"].get(stat_field, 0), reverse=reverse_sort
            )

        return {
            "users": result_users,
            "pagination": {
                "total": total_users,
                "limit": limit,
                "skip": skip,
                "has_more": (skip + limit) < total_users,
            },
        }

    except Exception as e:
        logging.error(f"Error in admin users endpoint: {str(e)}")
        logging.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/dashboard", dependencies=[Security(azure_scheme)])
async def get_admin_dashboard(
    user: User = Security(azure_scheme),
    months: int = Query(
        12, ge=1, le=24, description="Number of months of historical data"
    ),
    recent_jobs_limit: int = Query(
        10, ge=1, le=50, description="Number of recent jobs to return"
    ),
):
    """Admin endpoint to get dashboard analytics with aggregated job analysis statistics"""
    await track_user_login(users_collection, user)
    db_user = users_collection.find_one({"username": user.preferred_username})

    if not db_user or "admin" not in db_user["roles"]:
        raise HTTPException(status_code=403, detail="Admin access required")

    try:
        now = datetime.utcnow()
        start_date = now - timedelta(days=months * 30)

        # Overall Statistics
        overall_stats_pipeline = [
            {"$match": {"completed_at": {"$exists": True}}},
            {
                "$group": {
                    "_id": None,
                    "total_jobs": {"$sum": 1},
                    "total_files_processed": {"$sum": 1},
                    "total_data_bytes": {"$sum": "$file_size_bytes"},
                    "total_borehole_length": {"$sum": "$total_length_processed"},
                    "total_processing_time": {"$sum": "$processing_time_seconds"},
                    "total_structures": {"$sum": "$total_structures_detected"},
                    "total_fractures": {"$sum": {"$ifNull": ["$total_fractures", 0]}},
                    "total_breakouts": {"$sum": {"$ifNull": ["$total_breakouts", 0]}},
                    "avg_quality_score": {
                        "$avg": {"$ifNull": ["$average_quality_score", 0]}
                    },
                }
            },
        ]

        overall_stats = list(job_analysis_collection.aggregate(overall_stats_pipeline))

        if overall_stats:
            stats = overall_stats[0]
            avg_processing_time_minutes = (
                (stats["total_processing_time"] / stats["total_jobs"]) / 60
                if stats["total_jobs"] > 0
                else 0
            )
            total_data_gb = stats["total_data_bytes"] / (1024**3)
        else:
            stats = {
                "total_jobs": 0,
                "total_files_processed": 0,
                "total_data_bytes": 0,
                "total_borehole_length": 0,
                "total_processing_time": 0,
                "total_structures": 0,
                "total_fractures": 0,
                "total_breakouts": 0,
                "avg_quality_score": 0,
            }
            avg_processing_time_minutes = 0
            total_data_gb = 0

        # Monthly Statistics
        monthly_stats_pipeline = [
            {"$match": {"completed_at": {"$exists": True, "$gte": start_date}}},
            {
                "$group": {
                    "_id": {
                        "year": {"$year": "$completed_at"},
                        "month": {"$month": "$completed_at"},
                    },
                    "jobs": {"$sum": 1},
                    "structures": {"$sum": "$total_structures_detected"},
                    "data_bytes": {"$sum": "$file_size_bytes"},
                    "borehole_length": {"$sum": "$total_length_processed"},
                    "processing_time": {"$sum": "$processing_time_seconds"},
                }
            },
            {"$sort": {"_id.year": 1, "_id.month": 1}},
        ]

        monthly_data = list(job_analysis_collection.aggregate(monthly_stats_pipeline))

        formatted_monthly_data = []
        for item in monthly_data:
            month_name = calendar.month_abbr[item["_id"]["month"]]
            formatted_monthly_data.append(
                {
                    "month": month_name,
                    "year": item["_id"]["year"],
                    "jobs": item["jobs"],
                    "structures": item["structures"],
                    "dataGB": round(item["data_bytes"] / (1024**3), 2),
                    "boreholeLength": round(item["borehole_length"], 2),
                    "processingTimeHours": round(item["processing_time"] / 3600, 2),
                }
            )

        # Recent Jobs
        recent_jobs_cursor = (
            job_analysis_collection.find(
                {"completed_at": {"$exists": True}},
                {
                    "_id": 1,
                    "job_id": 1,
                    "fileName": 1,
                    "holeid": 1,
                    "user": 1,
                    "created_at": 1,
                    "completed_at": 1,
                    "processing_time_seconds": 1,
                    "file_size_bytes": 1,
                    "file_size_formatted": 1,
                    "hole_diameter_mm": 1,
                    "units": 1,
                    "start_depth": 1,
                    "end_depth": 1,
                    "total_length_processed": 1,
                    "total_structures_detected": 1,
                    "total_fractures": 1,
                    "total_breakouts": 1,
                    "average_quality_score": 1,
                },
            )
            .sort("completed_at", -1)
            .limit(recent_jobs_limit)
        )

        recent_jobs = []
        for job in recent_jobs_cursor:
            file_size_formatted = job.get("file_size_formatted")
            if not file_size_formatted and job.get("file_size_bytes"):
                bytes_val = job["file_size_bytes"]
                if bytes_val >= 1024**3:
                    file_size_formatted = f"{bytes_val / (1024**3):.2f} GB"
                elif bytes_val >= 1024**2:
                    file_size_formatted = f"{bytes_val / (1024**2):.2f} MB"
                elif bytes_val >= 1024:
                    file_size_formatted = f"{bytes_val / 1024:.2f} KB"
                else:
                    file_size_formatted = f"{bytes_val} B"

            recent_jobs.append(
                {
                    "_id": str(job["_id"]),
                    "job_id": job.get("job_id"),
                    "fileName": job.get("fileName"),
                    "hole_id": job.get("holeid"),
                    "user": job.get("user"),
                    "created_at": job.get("created_at"),
                    "completed_at": job.get("completed_at"),
                    "processing_time_seconds": job.get("processing_time_seconds", 0),
                    "file_size_bytes": job.get("file_size_bytes", 0),
                    "file_size_formatted": file_size_formatted,
                    "hole_diameter_mm": job.get("hole_diameter_mm"),
                    "units": job.get("units"),
                    "start_depth": job.get("start_depth"),
                    "end_depth": job.get("end_depth"),
                    "total_length_processed": job.get("total_length_processed", 0),
                    "total_structures_detected": job.get(
                        "total_structures_detected", 0
                    ),
                    "total_fractures": job.get("total_fractures", 0),
                    "total_breakouts": job.get("total_breakouts", 0),
                    "average_quality_score": job.get("average_quality_score", 0),
                }
            )

        # User Statistics
        user_stats_pipeline = [
            {"$match": {"completed_at": {"$exists": True}}},
            {
                "$group": {
                    "_id": "$user",
                    "job_count": {"$sum": 1},
                    "total_data_bytes": {"$sum": "$file_size_bytes"},
                    "total_length": {"$sum": "$total_length_processed"},
                    "last_activity": {"$max": "$completed_at"},
                }
            },
            {"$sort": {"job_count": -1}},
            {"$limit": 10},
        ]

        top_users = list(job_analysis_collection.aggregate(user_stats_pipeline))

        formatted_user_stats = []
        for user_stat in top_users:
            formatted_user_stats.append(
                {
                    "username": user_stat["_id"],
                    "job_count": user_stat["job_count"],
                    "total_data_gb": round(
                        user_stat["total_data_bytes"] / (1024**3), 2
                    ),
                    "total_length": round(user_stat["total_length"], 2),
                    "last_activity": user_stat["last_activity"],
                }
            )

        return {
            "summary": {
                "totalJobs": stats["total_jobs"],
                "totalFilesProcessed": stats["total_files_processed"],
                "totalDataProcessed": round(total_data_gb, 2),
                "totalBoreholeLength": round(stats["total_borehole_length"], 2),
                "avgProcessingTime": round(avg_processing_time_minutes, 2),
                "totalStructuresDetected": stats["total_structures"],
                "avgQualityScore": round(stats["avg_quality_score"], 3),
            },
            "monthlyData": formatted_monthly_data,
            "recentJobs": recent_jobs,
            "structuresCount": {
                "fractures": stats["total_fractures"],
                "breakouts": stats["total_breakouts"],
                "total": stats["total_structures"],
            },
            "topUsers": formatted_user_stats,
            "metadata": {
                "generated_at": now,
                "months_included": months,
                "recent_jobs_limit": recent_jobs_limit,
            },
        }

    except Exception as e:
        logging.error(f"Error in admin dashboard endpoint: {str(e)}")
        logging.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
