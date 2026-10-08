"""CRS (Coordinate Reference System) detection and transformation utilities.

This module implements automatic UTM zone detection for accurate metric
measurements. Given a geometry in geographic coordinates (e.g., EPSG:4326),
it determines the appropriate UTM zone based on the geometry's centroid
and provides reprojection functions.
"""

import logging

import numpy as np
from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry
import shapely

logger = logging.getLogger(__name__)

WGS84 = CRS.from_epsg(4326)

# Projected CRSs covering at most this many degrees of longitude are "local"
# (UTM zones are 6 degrees; State Plane zones are smaller).
MAX_LOCAL_CRS_SPAN_DEGREES = 30


def get_utm_crs(longitude: float, latitude: float) -> CRS:
    """Determine the appropriate UTM CRS for a given longitude/latitude.

    UTM zones divide the Earth into 60 zones, each 6 degrees of longitude wide.
    Northern and southern hemispheres use different EPSG code ranges:
    - Northern: EPSG:326xx
    - Southern: EPSG:327xx

    Args:
        longitude: Longitude in degrees (-180 to 180).
        latitude: Latitude in degrees (-90 to 90).

    Returns:
        A pyproj CRS object for the appropriate UTM zone.
    """
    # Clamp longitude to valid range
    longitude = max(-180.0, min(180.0, longitude))

    zone_number = int((longitude + 180) / 6) + 1
    # Clamp zone to valid range (1-60)
    zone_number = max(1, min(60, zone_number))

    if latitude >= 0:
        epsg_code = 32600 + zone_number  # Northern hemisphere
    else:
        epsg_code = 32700 + zone_number  # Southern hemisphere

    logger.debug(
        "UTM zone for (%.4f, %.4f): Zone %d %s -> EPSG:%d",
        longitude,
        latitude,
        zone_number,
        "N" if latitude >= 0 else "S",
        epsg_code,
    )

    return CRS.from_epsg(epsg_code)


def get_projected_crs_for_geometry(geometry: BaseGeometry, source_crs: CRS) -> CRS:
    """Determine the best projected CRS for measuring a geometry.

    A *local* projected source CRS (e.g. a UTM zone or State Plane zone) is
    returned directly; measurements are later converted to metres with
    `linear_unit_to_metres`, so CRSs in US feet etc. are handled.

    Geographic CRSs and *global* projections (Web Mercator, World Mercator, ...)
    are not trusted: Web Mercator overstates area by 4x at 60 degrees latitude.
    For those, the UTM zone is chosen from the geometry's centroid in
    WGS 84 longitude/latitude.

    Args:
        geometry: A Shapely geometry object.
        source_crs: The CRS of the input geometry.

    Returns:
        A projected CRS suitable for planar measurements.
    """
    if is_local_projected(source_crs):
        logger.debug("Source CRS is a local projected CRS: %s", source_crs.name)
        return source_crs

    if not (source_crs.is_geographic or source_crs.is_projected):
        raise ValueError(f"Cannot measure in non-geographic, non-projected CRS: {source_crs.name}")

    centroid = geometry.centroid
    lon, lat = centroid.x, centroid.y
    if source_crs.to_epsg() != 4326:
        to_wgs84 = Transformer.from_crs(source_crs, WGS84, always_xy=True)
        lon, lat = to_wgs84.transform(lon, lat)
    return get_utm_crs(lon, lat)


def is_local_projected(crs: CRS) -> bool:
    """Return True for projected CRSs designed for a limited region.

    A CRS whose area of use spans more than MAX_LOCAL_CRS_SPAN_DEGREES of
    longitude (or is unknown) is treated as global and therefore unsuitable
    for direct measurement.
    """
    if not crs.is_projected:
        return False
    area = crs.area_of_use
    if area is None:
        return False
    span = area.east - area.west
    if span < 0:  # Area of use crosses the antimeridian
        span += 360
    return span <= MAX_LOCAL_CRS_SPAN_DEGREES


def to_wgs84(geometry: BaseGeometry, source_crs: CRS) -> BaseGeometry:
    """Reproject a geometry to WGS 84 longitude/latitude (EPSG:4326)."""
    if source_crs.to_epsg() == 4326:
        return geometry
    return reproject_geometry(geometry, source_crs, WGS84)


def linear_unit_to_metres(crs: CRS) -> float:
    """Return the number of metres in one horizontal unit of a projected CRS.

    1.0 for metre-based CRSs; e.g. 0.3048006 for US survey feet.
    """
    axis = crs.axis_info[0]
    return axis.unit_conversion_factor or 1.0


def reproject_geometry(
    geometry: BaseGeometry, source_crs: CRS, target_crs: CRS
) -> BaseGeometry:
    """Reproject a Shapely geometry from one CRS to another.

    Args:
        geometry: The input Shapely geometry.
        source_crs: The current CRS of the geometry.
        target_crs: The desired target CRS.

    Returns:
        A new Shapely geometry in the target CRS.
    """
    if source_crs == target_crs:
        return geometry

    transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)

    def _transform_coords(coords):
        """Transform an (N, 2) ndarray of x/y coordinates."""
        x_out, y_out = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x_out, y_out])

    # Measurements are planar, so Z values are dropped during reprojection.
    return shapely.transform(geometry, _transform_coords)


def crs_to_string(crs: CRS) -> str:
    """Convert a CRS object to a human-readable string (e.g., 'EPSG:32643').

    Args:
        crs: A pyproj CRS object.

    Returns:
        A string representation like 'EPSG:32643'.
    """
    authority = crs.to_authority()
    if authority:
        return f"{authority[0]}:{authority[1]}"
    return crs.to_wkt()
