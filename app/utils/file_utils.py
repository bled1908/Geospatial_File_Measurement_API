"""File handling utilities for upload validation and extraction."""

import re
import zipfile
import logging
from pathlib import Path

from fastapi import UploadFile, HTTPException

import app.config as config

logger = logging.getLogger(__name__)

ZIP_CHUNK_SIZE = 1024 * 1024


def validate_file(file: UploadFile) -> str:
    """Validate the uploaded file and return the file type.

    Args:
        file: The uploaded file object.

    Returns:
        File type string: 'shapefile' for .zip, 'kml' for .kml.

    Raises:
        HTTPException: If the file is invalid.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")

    ext = Path(file.filename).suffix.lower()
    if ext not in config.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported file type '{ext}'. "
                f"Allowed types: {', '.join(sorted(config.ALLOWED_EXTENSIONS))}"
            ),
        )

    return "shapefile" if ext == ".zip" else "kml"


def safe_filename(filename: str) -> str:
    """Reduce a client-supplied filename to a safe basename.

    Strips any directory components (both / and \\ separators) and
    characters outside a conservative whitelist.
    """
    base = re.split(r"[\\/]", filename)[-1]
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base).lstrip(".")
    return base or "upload"


async def save_upload(file: UploadFile, file_id: str) -> str:
    """Save an uploaded file to disk.

    Args:
        file: The uploaded file object.
        file_id: Unique identifier for the file.

    Returns:
        The absolute path where the file was saved.

    Raises:
        HTTPException: If the file exceeds the size limit.
    """
    content = await file.read()
    if len(content) > config.MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File size exceeds the {config.MAX_FILE_SIZE // (1024 * 1024)} MB limit.",
        )
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    file_dir = config.UPLOAD_DIR / file_id
    file_dir.mkdir(parents=True, exist_ok=True)

    # Never trust the client-supplied name as a path (e.g. "../../evil.kml")
    file_path = file_dir / safe_filename(file.filename)

    with open(file_path, "wb") as f:
        f.write(content)

    logger.info("Saved file '%s' to %s (%d bytes)", file.filename, file_path, len(content))
    return str(file_path)


def _safe_extract(archive: zipfile.ZipFile, extract_dir: Path) -> None:
    """Extract a ZIP archive, rejecting unsafe paths and ZIP bombs.

    Sizes declared in ZIP headers can be forged, so the decompressed bytes
    are counted while streaming each member to disk.
    """
    members = [info for info in archive.infolist() if not info.is_dir()]
    for info in members:
        member_path = Path(info.filename)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise ValueError(f"Unsafe path in ZIP archive: {info.filename}")

    written = 0
    for info in members:
        target = extract_dir / info.filename
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as src, open(target, "wb") as dst:
            while chunk := src.read(ZIP_CHUNK_SIZE):
                written += len(chunk)
                if written > config.MAX_EXTRACTED_SIZE:
                    raise ValueError(
                        "ZIP archive is too large when extracted "
                        f"(limit {config.MAX_EXTRACTED_SIZE // (1024 * 1024)} MB)."
                    )
                dst.write(chunk)


def extract_shapefile(zip_path: str) -> str:
    """Extract a Shapefile from a ZIP archive.

    Args:
        zip_path: Path to the ZIP file.

    Returns:
        Path to the extracted .shp file.

    Raises:
        ValueError: If the ZIP does not contain a .shp file or is invalid.
    """
    zip_path = Path(zip_path)

    if not zipfile.is_zipfile(zip_path):
        raise ValueError(f"The file '{zip_path.name}' is not a valid ZIP archive.")

    extract_dir = zip_path.parent / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as z:
        _safe_extract(z, extract_dir)

    # Find the .shp file (search recursively), ignoring macOS metadata
    # such as __MACOSX/._data.shp that Finder adds to ZIP archives
    shp_files = sorted(
        path for path in extract_dir.rglob("*.shp")
        if "__MACOSX" not in path.parts and not path.name.startswith("._")
    )
    if not shp_files:
        raise ValueError(
            "No .shp file found in the uploaded ZIP archive. "
            "Please ensure the ZIP contains a valid Shapefile (.shp, .shx, .dbf)."
        )

    if len(shp_files) > 1:
        logger.warning(
            "Multiple .shp files found in ZIP; using the first one: %s", shp_files[0]
        )

    return str(shp_files[0])
