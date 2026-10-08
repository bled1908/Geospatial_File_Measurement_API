"""Measurement calculations for geospatial geometries.

This module calculates area for Polygon/MultiPolygon geometries
and length for LineString/MultiLineString geometries. Points and
unsupported geometry types are handled gracefully with no measurement.

All measurements are performed in a projected CRS (UTM) to ensure
accurate metric results, not degree-based calculations.
"""

import logging

from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app.services.crs_utils import (
    get_projected_crs_for_geometry,
    linear_unit_to_metres,
    reproject_geometry,
    crs_to_string,
)

logger = logging.getLogger(__name__)

# Geometry type groups
POLYGON_TYPES = {"Polygon", "MultiPolygon"}
LINE_TYPES = {"LineString", "MultiLineString"}
POINT_TYPES = {"Point", "MultiPoint"}
SUPPORTED_TYPES = POLYGON_TYPES | LINE_TYPES | POINT_TYPES


def calculate_measurements(
    geometry: BaseGeometry,
    geometry_type: str,
    source_crs: CRS,
) -> list[dict]:
    """Calculate measurements for a geometry based on its type.

    Args:
        geometry: A Shapely geometry object in the source CRS.
        geometry_type: The geometry type string (e.g., 'Polygon', 'LineString').
        source_crs: The CRS of the input geometry.

    Returns:
        A list of measurement dicts, each containing:
        - measurement_type: 'area' or 'length'
        - value: numeric value (rounded to 4 decimal places)
        - unit: 'square_meters' or 'meters'
        - projected_crs: the CRS used for calculation
    """
    if geometry_type in POINT_TYPES:
        # Points have no spatial measurement
        logger.debug("Point geometry — no measurement required.")
        return []

    if geometry_type not in POLYGON_TYPES | LINE_TYPES:
        # Unsupported geometry type (e.g., GeometryCollection)
        logger.warning(
            "Unsupported geometry type '%s' — skipping measurement.",
            geometry_type,
        )
        return []

    projected_crs = get_projected_crs_for_geometry(geometry, source_crs)
    projected_geom = reproject_geometry(geometry, source_crs, projected_crs)
    # Convert from the projected CRS's unit (usually metres, sometimes feet)
    metres_per_unit = linear_unit_to_metres(projected_crs)
    crs_label = crs_to_string(projected_crs)

    if geometry_type in POLYGON_TYPES:
        measurement = {
            "measurement_type": "area",
            "value": round(projected_geom.area * metres_per_unit**2, 4),
            "unit": "square_meters",
            "projected_crs": crs_label,
        }
    else:
        measurement = {
            "measurement_type": "length",
            "value": round(projected_geom.length * metres_per_unit, 4),
            "unit": "meters",
            "projected_crs": crs_label,
        }

    logger.info(
        "%s %s: %.4f %s (CRS: %s)",
        geometry_type,
        measurement["measurement_type"],
        measurement["value"],
        measurement["unit"],
        crs_label,
    )
    return [measurement]
