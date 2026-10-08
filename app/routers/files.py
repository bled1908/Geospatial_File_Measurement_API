"""API endpoints for geospatial file operations.

Endpoints:
    POST   /api/files/                  Upload a geospatial file
    GET    /api/files/                  List all uploaded files
    GET    /api/files/{file_id}/        Get file information
    GET    /api/files/{file_id}/measurements/  Get feature measurements
"""

import uuid
import logging

import shapely.wkt
from pyproj import CRS
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from shapely.geometry import mapping
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
import app.database as _db_module
from app.models import UploadedFile, Feature
from app.schemas import (
    FileUploadResponse,
    FileInfoResponse,
    FileMeasurementsResponse,
    FeatureMeasurement,
    MeasurementDetail,
)
from app.utils.file_utils import validate_file, save_upload
from app.services.crs_utils import to_wgs84
from app.services.file_processor import process_file

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/files", tags=["Files"])


# ---------------------------------------------------------------------------
# POST /api/files/ — Upload a file
# ---------------------------------------------------------------------------

@router.post("/", response_model=FileUploadResponse, status_code=201)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="A .zip (Shapefile) or .kml file"),
    db: Session = Depends(get_db),
):
    """Upload a geospatial file for processing.

    Accepts a `.zip` file containing a Shapefile or a `.kml` file.
    The file is saved and processing begins in the background.
    """
    # Validate file type
    file_type = validate_file(file)
    file_id = str(uuid.uuid4())

    # Save file to disk
    file_path = await save_upload(file, file_id)

    # Create database record
    file_record = UploadedFile(
        id=file_id,
        filename=file.filename,
        file_path=file_path,
        file_type=file_type,
        status="PENDING",
    )
    db.add(file_record)
    db.commit()
    db.refresh(file_record)

    logger.info("File '%s' uploaded (id: %s). Starting background processing.", file.filename, file_id)

    # Kick off background processing
    background_tasks.add_task(_process_in_background, file_id, file_path, file_type)

    return file_record


def _process_in_background(file_id: str, file_path: str, file_type: str) -> None:
    """Background task wrapper that creates its own database session."""
    db = _db_module.SessionLocal()
    try:
        process_file(file_id, file_path, file_type, db)
    except Exception as e:
        logger.error("Background processing error for '%s': %s", file_id, e, exc_info=True)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# GET /api/files/ — List all files
# ---------------------------------------------------------------------------

@router.get("/", response_model=list[FileInfoResponse])
def list_files(db: Session = Depends(get_db)):
    """List all uploaded files, ordered by most recent first."""
    files = (
        db.query(UploadedFile)
        .order_by(UploadedFile.created_at.desc())
        .all()
    )
    return files


# ---------------------------------------------------------------------------
# GET /api/files/{file_id}/ — File information
# ---------------------------------------------------------------------------

@router.get("/{file_id}/", response_model=FileInfoResponse)
def get_file_info(file_id: str, db: Session = Depends(get_db)):
    """Retrieve information about a specific uploaded file."""
    file_record = db.query(UploadedFile).filter(UploadedFile.id == file_id).first()
    if not file_record:
        raise HTTPException(status_code=404, detail="File not found.")
    return file_record


# ---------------------------------------------------------------------------
# GET /api/files/{file_id}/measurements/ — Feature measurements
# ---------------------------------------------------------------------------

@router.get(
    "/{file_id}/measurements/",
    response_model=FileMeasurementsResponse,
    responses={
        202: {"description": "File is still being processed"},
        404: {"description": "File not found"},
        422: {"description": "File processing failed"},
    },
)
def get_measurements(file_id: str, db: Session = Depends(get_db)):
    """Retrieve measurement results for all features in a file.

    Returns 202 if the file is still being processed,
    422 if processing failed, and 200 with measurements on success.
    """
    file_record = db.query(UploadedFile).filter(UploadedFile.id == file_id).first()
    if not file_record:
        raise HTTPException(status_code=404, detail="File not found.")

    # Handle non-completed states
    if file_record.status in ("PENDING", "PROCESSING"):
        return JSONResponse(
            status_code=202,
            content={
                "detail": (
                    f"File is still being processed. "
                    f"Current status: {file_record.status}. "
                    f"Please try again shortly."
                ),
                "status": file_record.status,
            },
        )

    if file_record.status == "FAILED":
        raise HTTPException(
            status_code=422,
            detail=f"File processing failed: {file_record.error_message}",
        )

    # Fetch features with their measurements in two queries (no N+1)
    features = (
        db.query(Feature)
        .options(selectinload(Feature.measurements))
        .filter(Feature.file_id == file_id)
        .order_by(Feature.feature_index)
        .all()
    )

    source_crs = CRS(file_record.crs)
    feature_measurements = [
        FeatureMeasurement(
            feature_index=feature.feature_index,
            geometry_type=feature.geometry_type,
            # GeoJSON (RFC 7946) coordinates are always WGS 84 lon/lat
            geometry=mapping(to_wgs84(shapely.wkt.loads(feature.geometry_wkt), source_crs)),
            properties=feature.properties,
            measurement_error=feature.measurement_error,
            measurements=[
                MeasurementDetail(
                    measurement_type=m.measurement_type,
                    value=m.value,
                    unit=m.unit,
                    projected_crs=m.projected_crs,
                )
                for m in feature.measurements
            ],
        )
        for feature in features
    ]

    return FileMeasurementsResponse(
        file_id=file_record.id,
        filename=file_record.filename,
        crs=file_record.crs,
        feature_count=file_record.feature_count,
        features=feature_measurements,
    )
