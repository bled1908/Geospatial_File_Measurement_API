"""Pydantic schemas for API request/response validation."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# File Schemas
# ---------------------------------------------------------------------------

class FileUploadResponse(BaseModel):
    """Response schema for file upload."""

    id: str
    filename: str
    file_type: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class FileInfoResponse(BaseModel):
    """Response schema for file information."""

    id: str
    filename: str
    file_type: str
    feature_count: int
    crs: str | None = None
    status: str
    error_message: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Measurement Schemas
# ---------------------------------------------------------------------------

class MeasurementDetail(BaseModel):
    """A single measurement result."""

    measurement_type: str = Field(..., description="Type of measurement: 'area' or 'length'")
    value: float = Field(..., description="Numeric measurement value")
    unit: str = Field(..., description="Unit of measurement: 'square_meters' or 'meters'")
    projected_crs: str = Field(..., description="The projected CRS used for this measurement")


class FeatureMeasurement(BaseModel):
    """Measurement results for a single feature."""

    feature_index: int
    geometry_type: str
    geometry: dict[str, Any] = Field(
        ..., description="Feature geometry as GeoJSON (WGS 84 longitude/latitude)"
    )
    properties: dict[str, Any] | None = None
    measurements: list[MeasurementDetail] = Field(default_factory=list)
    measurement_error: str | None = Field(
        None, description="Why this feature could not be measured, if it failed"
    )


class FileMeasurementsResponse(BaseModel):
    """Response schema for file measurements endpoint."""

    file_id: str
    filename: str
    crs: str | None = None
    feature_count: int
    features: list[FeatureMeasurement]
