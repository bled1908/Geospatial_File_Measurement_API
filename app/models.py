"""SQLAlchemy ORM models for geospatial data."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    Text,
    DateTime,
    ForeignKey,
    JSON,
)
from sqlalchemy.orm import relationship

from app.database import Base


def generate_uuid() -> str:
    """Generate a new UUID string."""
    return str(uuid.uuid4())


def utcnow() -> datetime:
    """Return current UTC time."""
    return datetime.now(timezone.utc)


class UploadedFile(Base):
    """Represents an uploaded geospatial file."""

    __tablename__ = "uploaded_files"

    id = Column(String, primary_key=True, default=generate_uuid)
    filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    file_type = Column(String, nullable=False)  # "shapefile" or "kml"
    crs = Column(String, nullable=True)
    feature_count = Column(Integer, default=0)
    status = Column(String, default="PENDING")  # PENDING | PROCESSING | COMPLETED | FAILED
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    features = relationship(
        "Feature", back_populates="file", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<UploadedFile(id={self.id}, filename={self.filename}, status={self.status})>"


class Feature(Base):
    """Represents a single geospatial feature extracted from a file."""

    __tablename__ = "features"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(String, ForeignKey("uploaded_files.id"), nullable=False)
    feature_index = Column(Integer, nullable=False)
    geometry_type = Column(String, nullable=False)
    geometry_wkt = Column(Text, nullable=False)
    properties = Column(JSON, nullable=True)
    measurement_error = Column(Text, nullable=True)  # Set if measuring failed

    file = relationship("UploadedFile", back_populates="features")
    measurements = relationship(
        "Measurement", back_populates="feature", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Feature(id={self.id}, type={self.geometry_type}, index={self.feature_index})>"


class Measurement(Base):
    """Stores measurement results for a feature."""

    __tablename__ = "measurements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    feature_id = Column(Integer, ForeignKey("features.id"), nullable=False)
    measurement_type = Column(String, nullable=False)  # "area" or "length"
    value = Column(Float, nullable=False)
    unit = Column(String, nullable=False)  # "square_meters" or "meters"
    projected_crs = Column(String, nullable=False)

    feature = relationship("Feature", back_populates="measurements")

    def __repr__(self) -> str:
        return f"<Measurement(type={self.measurement_type}, value={self.value} {self.unit})>"
