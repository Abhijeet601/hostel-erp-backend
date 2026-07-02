"""Photo upload service for storing student photos in object storage."""

import base64
import logging

from app.r2_storage import get_r2_service

logger = logging.getLogger(__name__)


def extract_base64_image(data_uri: str) -> tuple[bytes, str] | None:
    """Extract image bytes and content type from a data URI.
    
    Handles data URIs like: data:image/jpeg;base64,/9j/4AAQSkZJRg...
    Returns (bytes, content_type) or None if invalid.
    """
    if not data_uri or not isinstance(data_uri, str):
        return None
    
    if not data_uri.startswith("data:"):
        return None
    
    try:
        # Parse: data:image/jpeg;base64,<data>
        header, data = data_uri.split(",", 1)
        content_type = header.split(":")[1].split(";")[0]
        
        # Decode base64
        image_bytes = base64.b64decode(data)
        return image_bytes, content_type
    except (ValueError, IndexError) as e:
        logger.warning("Failed to parse data URI: %s", e)
        return None


def upload_student_photo(student_id: int, photo_data: str) -> str | None:
    """Upload a student photo to object storage.
    
    Args:
        student_id: The student ID
        photo_data: Base64-encoded data URI (e.g., data:image/jpeg;base64,...)
    
    Returns:
        Public URL of the uploaded photo, or None if upload failed.
    """
    if not photo_data:
        return None
    
    extracted = extract_base64_image(photo_data)
    if not extracted:
        logger.warning("Invalid photo data for student %d", student_id)
        return None
    
    image_bytes, content_type = extracted
    
    try:
        r2_service = get_r2_service()
        if not r2_service.enabled:
            logger.warning("R2 storage not enabled; cannot upload photo for student %d", student_id)
            return None
        
        # Generate a key based on student ID and content type
        ext = content_type.split("/")[-1] if "/" in content_type else "jpg"
        key = f"photos/student_{student_id}.{ext}"
        
        # Upload to R2
        url = r2_service.upload_bytes(image_bytes, key, content_type)
        logger.info("Uploaded photo for student %d to %s", student_id, key)
        return url
    except Exception as e:
        logger.error("Failed to upload photo for student %d: %s", student_id, e)
        return None


def delete_student_photo(student_id: int) -> None:
    """Delete a student's photo from object storage."""
    try:
        r2_service = get_r2_service()
        if not r2_service.enabled:
            return
        
        # Try common extensions
        for ext in ["jpg", "jpeg", "png", "gif", "webp"]:
            key = f"photos/student_{student_id}.{ext}"
            if r2_service.file_exists(key):
                r2_service.delete_file(key)
                logger.info("Deleted photo for student %d", student_id)
                return
    except Exception as e:
        logger.error("Failed to delete photo for student %d: %s", student_id, e)

