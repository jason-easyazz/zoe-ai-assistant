"""
Media Upload Router
Handles photo uploads with compression, HEIC conversion, thumbnails, and EXIF preservation
"""
from fastapi import APIRouter, UploadFile, File, HTTPException, Query, Form, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from PIL import Image, ExifTags
from PIL.Image import Resampling
import os
import io
import hashlib
from datetime import datetime
from pathlib import Path
import sqlite3
import json
import logging
from auth_integration import validate_session, AuthenticatedSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/media", tags=["media"])

# Configuration
UPLOAD_BASE_DIR = Path(os.getenv("UPLOAD_DIR", "/app/data/uploads"))
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB (increased to match nginx limit)
MAX_DIMENSION = 1920
THUMBNAIL_SIZE = 400
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif"}
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

# Try to import HEIC support
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIC_SUPPORTED = True
except ImportError:
    HEIC_SUPPORTED = False

# Response Models
class PhotoUploadResponse(BaseModel):
    photo_id: str
    url: str
    thumbnail_url: str
    filename: str
    size_bytes: int
    width: int
    height: int
    exif_data: Optional[Dict[str, Any]] = None

def init_media_db():
    """Initialize media tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS uploaded_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id TEXT UNIQUE NOT NULL,
            user_id TEXT DEFAULT 'default',
            filename TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            thumbnail_path TEXT NOT NULL,
            url TEXT NOT NULL,
            thumbnail_url TEXT NOT NULL,
            size_bytes INTEGER,
            width INTEGER,
            height INTEGER,
            format TEXT,
            exif_data JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_photos_user ON uploaded_photos(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_photos_id ON uploaded_photos(photo_id)")
    
    conn.commit()
    conn.close()

# Initialize on import
init_media_db()

def extract_exif_data(image: Image.Image) -> Dict[str, Any]:
    """Extract and parse EXIF data including GPS location"""
    exif_data = {}
    
    try:
        exif = image.getexif()
        
        if exif:
            for tag_id, value in exif.items():
                tag = ExifTags.TAGS.get(tag_id, tag_id)
                
                # Convert bytes to string
                if isinstance(value, bytes):
                    try:
                        value = value.decode('utf-8', errors='ignore')
                    except:
                        value = str(value)
                
                # Store important EXIF data
                if tag in ['DateTime', 'DateTimeOriginal', 'DateTimeDigitized', 
                          'Make', 'Model', 'Software', 'Orientation']:
                    exif_data[tag] = str(value)
            
            # Extract GPS data if available
            gps_info = exif.get_ifd(0x8825)  # GPS IFD
            if gps_info:
                gps_data = {}
                for tag_id, value in gps_info.items():
                    tag = ExifTags.GPSTAGS.get(tag_id, tag_id)
                    gps_data[tag] = str(value)
                
                if gps_data:
                    exif_data['GPSInfo'] = gps_data
                    
                    # Try to extract lat/lng
                    try:
                        lat, lng = _parse_gps_location(gps_data)
                        if lat and lng:
                            exif_data['latitude'] = lat
                            exif_data['longitude'] = lng
                    except:
                        pass
    except Exception as e:
        print(f"EXIF extraction warning: {e}")
    
    return exif_data

def _parse_gps_location(gps_data: Dict) -> tuple:
    """Parse GPS coordinates from EXIF data"""
    # This is a simplified GPS parser
    # Real implementation would handle DMS to decimal conversion
    lat_ref = gps_data.get('GPSLatitudeRef', 'N')
    lng_ref = gps_data.get('GPSLongitudeRef', 'E')
    
    # Return None for now - would need proper DMS parsing
    return None, None

def generate_photo_id() -> str:
    """Generate unique photo ID"""
    timestamp = datetime.now().isoformat()
    random_hash = hashlib.sha256(timestamp.encode()).hexdigest()[:12]
    return f"photo_{random_hash}"

def get_file_extension(filename: str) -> str:
    """Get file extension in lowercase, handle edge cases"""
    if not filename:
        return ""
    # Use pathlib to extract extension (handles spaces correctly)
    ext = Path(filename).suffix.lower()
    # If no extension found, try splitting by last dot
    if not ext and '.' in filename:
        ext = '.' + filename.rsplit('.', 1)[-1].lower()
    return ext

def process_image(
    image_data: bytes,
    original_filename: str,
    max_dimension: int = MAX_DIMENSION
) -> tuple:
    """
    Process image: convert HEIC if needed, compress, extract EXIF
    Returns: (processed_image, exif_data, format)
    """
    # Open image
    image = Image.open(io.BytesIO(image_data))
    
    # Extract EXIF before any processing
    exif_data = extract_exif_data(image)
    
    # Convert RGBA to RGB if needed
    if image.mode in ('RGBA', 'LA', 'P'):
        background = Image.new('RGB', image.size, (255, 255, 255))
        if image.mode == 'P':
            image = image.convert('RGBA')
        background.paste(image, mask=image.split()[-1] if image.mode in ('RGBA', 'LA') else None)
        image = background
    
    # Resize if needed
    if max(image.size) > max_dimension:
        ratio = max_dimension / max(image.size)
        new_size = tuple(int(dim * ratio) for dim in image.size)
        image = image.resize(new_size, Resampling.LANCZOS)
    
    # Determine output format (convert HEIC to JPEG)
    ext = get_file_extension(original_filename)
    output_format = 'JPEG' if ext in ['.heic', '.heif'] else image.format or 'JPEG'
    
    return image, exif_data, output_format

def create_thumbnail(image: Image.Image, size: int = THUMBNAIL_SIZE) -> Image.Image:
    """Create thumbnail maintaining aspect ratio"""
    thumbnail = image.copy()
    thumbnail.thumbnail((size, size), Resampling.LANCZOS)
    return thumbnail

async def save_photo(
    image: Image.Image,
    user_id: str,
    original_filename: str,
    output_format: str,
    exif_data: Dict
) -> tuple:
    """
    Save processed image and thumbnail to disk
    Returns: (file_path, thumbnail_path, url, thumbnail_url)
    """
    # Create directory structure: /uploads/journal/{user_id}/{year}/{month}/
    now = datetime.now()
    year = now.strftime("%Y")
    month = now.strftime("%m")
    
    upload_dir = UPLOAD_BASE_DIR / "journal" / user_id / year / month
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate unique filename
    photo_id = generate_photo_id()
    ext = ".jpg" if output_format == "JPEG" else f".{output_format.lower()}"
    filename = f"{photo_id}{ext}"
    thumbnail_filename = f"{photo_id}_thumb{ext}"
    
    file_path = upload_dir / filename
    thumbnail_path = upload_dir / thumbnail_filename
    
    # Save main image
    if output_format == "JPEG":
        image.save(file_path, format=output_format, quality=85, optimize=True)
    else:
        image.save(file_path, format=output_format, optimize=True)
    
    # Save thumbnail
    thumbnail = create_thumbnail(image)
    if output_format == "JPEG":
        thumbnail.save(thumbnail_path, format=output_format, quality=80, optimize=True)
    else:
        thumbnail.save(thumbnail_path, format=output_format, optimize=True)
    
    # Generate URLs
    url = f"/uploads/journal/{user_id}/{year}/{month}/{filename}"
    thumbnail_url = f"/uploads/journal/{user_id}/{year}/{month}/{thumbnail_filename}"
    
    return str(file_path), str(thumbnail_path), url, thumbnail_url, photo_id

@router.post("/upload", response_model=List[PhotoUploadResponse])
async def upload_photos(
    files: List[UploadFile] = File(...),
    user_id: str = Form("default")
):
    """
    Upload one or more photos with automatic compression and HEIC conversion
    Supports: JPG, PNG, GIF, WEBP, HEIC (iPhone), HEIF
    """
    if len(files) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 files per upload")
    
    uploaded_photos = []
    
    for file in files:
        # Log what we received
        logger.info(f"📸 Upload received: filename='{file.filename}', content_type='{file.content_type}', size={file.size if hasattr(file, 'size') else 'unknown'}")
        
        # Validate file extension
        ext = get_file_extension(file.filename)
        logger.info(f"📸 Extracted extension: '{ext}' from filename '{file.filename}'")
        
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"File type '{ext}' not supported. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            )
        
        # Check HEIC support
        if ext in ['.heic', '.heif'] and not HEIC_SUPPORTED:
            raise HTTPException(
                status_code=400,
                detail="HEIC/HEIF support not installed. Install pillow-heif package."
            )
        
        # Read file data
        file_data = await file.read()
        
        # Check file size
        if len(file_data) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"File {file.filename} exceeds {MAX_FILE_SIZE / 1024 / 1024}MB limit"
            )
        
        try:
            # Process image
            image, exif_data, output_format = process_image(file_data, file.filename)
            
            # Save to disk
            file_path, thumbnail_path, url, thumbnail_url, photo_id = await save_photo(
                image, user_id, file.filename, output_format, exif_data
            )
            
            # Get file info
            width, height = image.size
            size_bytes = len(file_data)
            
            # Save to database
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO uploaded_photos 
                (photo_id, user_id, filename, original_filename, file_path, thumbnail_path,
                 url, thumbnail_url, size_bytes, width, height, format, exif_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                photo_id, user_id, Path(file_path).name, file.filename,
                file_path, thumbnail_path, url, thumbnail_url,
                size_bytes, width, height, output_format,
                json.dumps(exif_data) if exif_data else None
            ))
            
            conn.commit()
            conn.close()
            
            uploaded_photos.append(PhotoUploadResponse(
                photo_id=photo_id,
                url=url,
                thumbnail_url=thumbnail_url,
                filename=Path(file_path).name,
                size_bytes=size_bytes,
                width=width,
                height=height,
                exif_data=exif_data if exif_data else None
            ))
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error processing {file.filename}: {str(e)}")
    
    return uploaded_photos

@router.delete("/photo/{photo_id}")
async def delete_photo(
    photo_id: str,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Delete a photo and its thumbnail"""
    user_id = session.user_id
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get photo info
    cursor.execute("""
        SELECT file_path, thumbnail_path
        FROM uploaded_photos
        WHERE photo_id = ? AND user_id = ?
    """, (photo_id, user_id))
    
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Photo not found")
    
    file_path, thumbnail_path = row
    
    # Delete from database
    cursor.execute("DELETE FROM uploaded_photos WHERE photo_id = ? AND user_id = ?", (photo_id, user_id))
    conn.commit()
    conn.close()
    
    # Delete files from disk
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
        if os.path.exists(thumbnail_path):
            os.remove(thumbnail_path)
    except Exception as e:
        print(f"Warning: Could not delete files: {e}")
    
    return {"message": "Photo deleted successfully", "photo_id": photo_id}

@router.get("/photo/{photo_id}")
async def get_photo_info(
    photo_id: str,
    session: AuthenticatedSession = Depends(validate_session)
):
    """Get photo metadata"""
    user_id = session.user_id
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT photo_id, filename, url, thumbnail_url, size_bytes, width, height,
               format, exif_data, created_at
        FROM uploaded_photos
        WHERE photo_id = ? AND user_id = ?
    """, (photo_id, user_id))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Photo not found")
    
    return {
        "photo_id": row[0],
        "filename": row[1],
        "url": row[2],
        "thumbnail_url": row[3],
        "size_bytes": row[4],
        "width": row[5],
        "height": row[6],
        "format": row[7],
        "exif_data": json.loads(row[8]) if row[8] else None,
        "created_at": row[9]
    }




