# app/db.py
import os
import logging
from azure.storage.blob import BlobServiceClient
from pymongo import MongoClient

logging.basicConfig(level=logging.INFO)

# Database connection strings and configuration
BLOB_CONN_STRING = os.getenv("BLOB_CONN_STRING")
BLOB_CONTAINER_NAME = os.getenv("BLOB_CONTAINER_NAME")
MONGODB_CONN_STRING = os.getenv("MONGODB_CONN_STRING")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME")
MONGODB_DB_USER = os.getenv("MONGODB_DB_USER")
MONGODB_DB_PASSWORD = os.getenv("MONGODB_DB_PASSWORD")
SERVICEBUS_CONN_STRING = os.getenv("SERVICEBUS_CONN_STRING")
SERVICEBUS_QUEUE_NAME = os.getenv("SERVICEBUS_QUEUE_NAME")
RESOURCE_GROUP = os.getenv("RESOURCE_GROUP")


# Initialize clients
blob_service_client = BlobServiceClient.from_connection_string(BLOB_CONN_STRING)
mongo_client = MongoClient(MONGODB_CONN_STRING)
db = mongo_client[MONGODB_DB_NAME]

# Collections
jobs_collection = db["jobs"]
users_collection = db["users"]
job_analysis_collection = db["jobAnalysisRecords"]

# Ensure blob container exists
try:
    container_client = blob_service_client.get_container_client(BLOB_CONTAINER_NAME)
    if not container_client.exists():
        container_client = blob_service_client.create_container(BLOB_CONTAINER_NAME)
except Exception as e:
    logging.error(f"Error setting up blob container: {str(e)}")
