import logging
from utils.database import Database

logger = logging.getLogger(__name__)

class GrowingProfileController:
    def __init__(self, db: Database):
        self.db = db

    def get_growing_profiles(self):
        """Retrieve all growing profiles"""
        try:
            profiles = self.db.get_growing_profiles()
            return profiles
        except Exception as e:
            logger.error(f"Error retrieving growing profiles: {e}")
            return []

    def save_growing_profile(self, profile_data):
        """Save a growing profile"""
        try:
            success = self.db.save_growing_profile(profile_data)
            return success
        except Exception as e:
            logger.error(f"Error saving growing profile: {e}")
            return False

    def get_growing_profile(self, profile_id):
        """Retrieve a specific growing profile"""
        try:
            profile = self.db.get_growing_profile(profile_id)
            return profile
        except Exception as e:
            logger.error(f"Error retrieving growing profile: {e}")
            return None