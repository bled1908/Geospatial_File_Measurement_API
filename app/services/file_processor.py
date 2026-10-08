"""File processing orchestrator.

This module coordinates the complete processing pipeline:
1. Update file status to PROCESSING
2. Read geospatial data from the file
3. Extract features and their properties
4. Calculate measurements for each feature
5. Store results in the database
6. Update file status to COMPLETED (or FAILED on error)
"""

import logging

from pyproj import CRS
from sqlalchemy.orm import Session

from app.models import UploadedFile, Feature, Measurement
from app.services.geo_reader import read_geospatial_file
from app.services.measurement import calculate_measurements

logger = logging.getLogger(__name__)


def process_file(file_id: str, file_path: str, file_type: str, db: Session) -> None:
    """Process an uploaded geospatial file end-to-end.

    This function is designed to be called as a background task.
    It manages its own error handling and status updates.

    Args:
        file_id: UUID of the uploaded file record.
        file_path: Path to the uploaded file on disk.
        file_type: File type ('shapefile' or 'kml').
        db: SQLAlchemy database session.
    """
    file_record = db.query(UploadedFile).filter(UploadedFile.id == file_id).first()
    if not file_record:
        logger.error("File record '%s' not found in database.", file_id)
        return

    try:
        # --- Step 1: Mark as processing ---
        file_record.status = "PROCESSING"
        db.commit()
        logger.info("Processing file '%s' (id: %s)", file_record.filename, file_id)

        # --- Step 2: Read geospatial data ---
        data = read_geospatial_file(file_path, file_type)

        crs_string = data["crs"]
        source_crs = CRS(crs_string)
        raw_features = data["features"]

        # --- Step 3: Update file metadata ---
        file_record.crs = crs_string
        file_record.feature_count = len(raw_features)

        # --- Step 4: Process each feature ---
        for feat_data in raw_features:
            feature = Feature(
                file_id=file_id,
                feature_index=feat_data["index"],
                geometry_type=feat_data["geometry_type"],
                geometry_wkt=feat_data["geometry"].wkt,
                properties=feat_data["properties"],
            )
            db.add(feature)
            db.flush()  # Assign the auto-generated feature.id

            # --- Step 5: Calculate measurements ---
            try:
                measurements = calculate_measurements(
                    feat_data["geometry"],
                    feat_data["geometry_type"],
                    source_crs,
                )
                for m in measurements:
                    db.add(Measurement(
                        feature_id=feature.id,
                        measurement_type=m["measurement_type"],
                        value=m["value"],
                        unit=m["unit"],
                        projected_crs=m["projected_crs"],
                    ))
            except Exception as e:
                logger.warning(
                    "Measurement calculation failed for feature %d "
                    "(type: %s): %s",
                    feat_data["index"],
                    feat_data["geometry_type"],
                    e,
                )
                # Record the failure and continue with the remaining features
                feature.measurement_error = str(e)

        # --- Step 6: Mark as completed ---
        file_record.status = "COMPLETED"
        db.commit()
        logger.info(
            "Successfully processed file '%s': %d features extracted.",
            file_record.filename,
            len(raw_features),
        )

    except Exception as e:
        logger.error("File processing failed for '%s': %s", file_id, e, exc_info=True)
        db.rollback()
        file_record.status = "FAILED"
        file_record.error_message = str(e)
        db.commit()
