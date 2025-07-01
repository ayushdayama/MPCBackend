import hashlib
import logging
from firebase_admin import firestore
from typing import Optional, Dict

db = firestore.client()


def hash_password(password: str) -> str:
    """Hash a password using SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()


def create_user(username: str, password: str) -> Dict:
    """
    Create a new user in the cycle-sense-users collection
    Returns: Dict with success status and message/error
    """
    try:
        # Check if username already exists
        users_ref = db.collection("cycle-sense-users")
        if any(doc.to_dict()["username"] == username for doc in users_ref.stream()):
            return {"success": False, "error": "Username already exists"}

        # Hash the password
        hashed_password = hash_password(password)

        # Create user document with username as document ID
        user_data = {
            "username": username,
            "password": hashed_password
        }

        users_ref.document(username).set(user_data)

        # Initialize user's menstrual data document in 'menstrual_data' collection
        db.collection("menstrual_data").document(username).set({})

        return {"success": True, "message": "User created successfully"}
    except Exception as e:
        logging.error(f"Error creating user: {str(e)}")
        return {"success": False, "error": str(e)}


def validate_login(username: str, password: str) -> Dict:
    """
    Validate user login credentials
    Returns: Dict with validation result and user's collection name if successful
    """
    try:
        users_ref = db.collection("cycle-sense-users")
        users = users_ref.stream()

        hashed_password = hash_password(password)

        for user_doc in users:
            user_data = user_doc.to_dict()
            if user_data["username"] == username and user_data["password"] == hashed_password:
                return {
                    "success": True
                }

        return {"success": False, "error": "Invalid credentials"}
    except Exception as e:
        logging.error(f"Error validating login: {str(e)}")
        return {"success": False, "error": str(e)}


def get_user_collection(username: str) -> Optional[str]:
    """Get the collection name for a given username"""
    # This function is no longer needed with the new structure, but kept for compatibility
    # Returns the username as the document ID for user's data
    return username
