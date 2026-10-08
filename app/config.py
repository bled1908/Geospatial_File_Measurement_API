"""Application configuration settings."""

import os
from pathlib import Path

# Base directory of the project
BASE_DIR = Path(__file__).resolve().parent.parent

# Directory for storing uploaded files
UPLOAD_DIR = BASE_DIR / "uploads"

# SQLite database URL
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{BASE_DIR / 'geospatial.db'}"
)

# Allowed file extensions
ALLOWED_EXTENSIONS = {".zip", ".kml"}

# Maximum file size (50 MB)
MAX_FILE_SIZE = 50 * 1024 * 1024

# Maximum total uncompressed size of a Shapefile ZIP (ZIP-bomb guard)
MAX_EXTRACTED_SIZE = 500 * 1024 * 1024
