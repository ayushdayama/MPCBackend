import hashlib
import logging
from firebase_admin import firestore
from typing import Optional, Dict
import sys

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

        # Accept security question and answer from global context (set by FastAPI endpoint)
        # Use global context for security question/answer if available
        security_question = None
        security_answer = None
        # Try both app.main and __main__ for context
        main_mod = sys.modules.get('app.main')
        if not main_mod:
            main_mod = sys.modules.get('__main__')
        if main_mod and hasattr(main_mod, '_SECURITY_CONTEXT') and main_mod._SECURITY_CONTEXT:
            security_question = main_mod._SECURITY_CONTEXT.get('security_question')
            security_answer = main_mod._SECURITY_CONTEXT.get('security_answer')
        user_data = {
            "username": username,
            "password": hashed_password,
            "securityQuestion": security_question,
            "securityAnswer": security_answer,
        }

        users_ref.document(username).set(user_data)

        # Initialize user's menstrual data document in 'menstrual_data' collection
        db.collection("menstrual_data").document(username).set({})

        return {"success": True, "message": "User created successfully"}
    except Exception as e:
        logging.error(f"Error creating user: {str(e)}")
        return {"success": False, "error": str(e)}


def get_security_question(username: str) -> Dict:
    """Get the security question for a given username."""
    try:
        user_doc = db.collection("cycle-sense-users").document(username).get()
        if not user_doc.exists:
            return {"success": False, "error": "User not found"}
        user_data = user_doc.to_dict() or {}
        return {"success": True, "securityQuestion": user_data.get("securityQuestion")}
    except Exception as e:
        logging.error(f"Error getting security question: {str(e)}")
        return {"success": False, "error": str(e)}


def verify_security_answer_and_reset(username: str, security_answer: str, new_password: str) -> Dict:
    """Verify the security answer and reset the password if correct."""
    try:
        user_ref = db.collection("cycle-sense-users").document(username)
        user_doc = user_ref.get()
        if not user_doc.exists:
            return {"success": False, "error": "User not found"}
        user_data = user_doc.to_dict() or {}
        if user_data.get("securityAnswer") != security_answer.lower():
            return {"success": False, "error": "Incorrect security answer"}
        # Reset password
        hashed_password = hash_password(new_password)
        user_ref.update({"password": hashed_password})
        return {"success": True, "message": "Password reset successfully"}
    except Exception as e:
        logging.error(f"Error resetting password: {str(e)}")
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
