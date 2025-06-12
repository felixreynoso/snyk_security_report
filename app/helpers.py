from fastapi_azure_auth.user import User
import logging
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO)


async def track_user_login(users_collection, user: User):
    """
    Track user login data in the users collection.
    Creates a new user document if the user doesn't exist.
    Updates the lastLogin date if the user exists.
    """
    now = datetime.utcnow()

    # Try to find the user
    existing_user = users_collection.find_one({"username": user.preferred_username})

    if existing_user:
        # User exists, update lastLogin
        users_collection.update_one(
            {"username": user.preferred_username}, {"$set": {"lastLogin": now}}
        )
        logging.info(f"Updated login timestamp for user: {user.preferred_username}")
    else:
        # User doesn't exist, create new record
        new_user = {
            "username": user.preferred_username,
            "email": user.email if hasattr(user, "email") else None,
            "name": user.name if hasattr(user, "name") else None,
            "createdDate": now,
            "lastLogin": now,
            "quota": 100,  # Default quota
            "roles": ["user"],  # Default role
            "status": "active",
        }
        users_collection.insert_one(new_user)
        logging.info(f"Created new user record for: {user.preferred_username}")

    return True
