"""Geospatial file readers for Shapefile and KML formats.

This module provides a unified interface for reading geospatial features
from different file formats. Shapefiles are read via GeoPandas (pyogrio/GDAL).
KML files are read via GeoPandas with a fallback to manual XML parsing
using lxml for maximum compatibility.

KML documents frequently split placemarks across several <Folder> elements,
which GDAL exposes as separate layers, so every layer is read and merged.
"""

import json
import logging

import geopandas as gpd
import pandas as pd
import pyogrio
from pyproj import CRS
from shapely.geometry import (
    GeometryCollection,
    LinearRing,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)

from app.utils.file_utils import extract_shapefile

logger = logging.getLogger(__name__)

KML_NS = "http://www.opengis.net/kml/2.2"

# KML styling/rendering fields that GDAL exposes as columns but carry no
# attribute information about the feature itself.
KML_STYLE_FIELDS = {
    "tessellate", "extrude", "visibility", "drawOrder", "icon", "altitudeMode",
}


def read_geospatial_file(file_path: str, file_type: str) -> dict:
    """Read a geospatial file and return parsed feature data.

    Args:
        file_path: Path to the geospatial file.
        file_type: Either 'shapefile' or 'kml'.

    Returns:
        A dict with:
        - crs: CRS string (e.g., 'EPSG:4326')
        - features: list of feature dicts, each with:
            - index: feature index (0-based position in the source file)
            - geometry_type: geometry type string
            - geometry: Shapely geometry object
            - properties: dict of feature attributes

    Raises:
        ValueError: If the file type is unsupported or the file cannot be read.
    """
    if file_type == "shapefile":
        return _read_shapefile(file_path)
    elif file_type == "kml":
        return _read_kml(file_path)
    else:
        raise ValueError(f"Unsupported file type: {file_type}")


# ---------------------------------------------------------------------------
# Shapefile Reader
# ---------------------------------------------------------------------------

def _read_shapefile(zip_path: str) -> dict:
    """Read features from a Shapefile ZIP archive."""
    shp_path = extract_shapefile(zip_path)
    logger.info("Reading Shapefile: %s", shp_path)
    gdf = gpd.read_file(shp_path)

    crs_string = _crs_to_string(gdf.crs)
    if crs_string is None:
        crs_string = _assume_wgs84_if_plausible(gdf)

    return {"crs": crs_string, "features": _extract_features(gdf)}


def _assume_wgs84_if_plausible(gdf: gpd.GeoDataFrame) -> str:
    """Handle a Shapefile without a .prj file.

    Coordinates that fit within longitude/latitude bounds are assumed to be
    WGS 84 (the overwhelmingly common case). Anything else cannot be measured
    reliably, so processing fails with a clear message instead of guessing.
    """
    if gdf.empty:
        return "EPSG:4326"
    min_x, min_y, max_x, max_y = gdf.total_bounds
    if -180 <= min_x <= max_x <= 180 and -90 <= min_y <= max_y <= 90:
        logger.warning("Shapefile has no .prj file; assuming EPSG:4326.")
        return "EPSG:4326"
    raise ValueError(
        "The Shapefile has no .prj file and its coordinates are not "
        "longitude/latitude, so its CRS cannot be determined. "
        "Please include the .prj file in the ZIP archive."
    )


# ---------------------------------------------------------------------------
# KML Reader (with fallback)
# ---------------------------------------------------------------------------

def _read_kml(kml_path: str) -> dict:
    """Read features from a KML file.

    Attempts GeoPandas with the LIBKML driver first, then the KML driver,
    then falls back to manual XML parsing for maximum compatibility.
    KML is always WGS 84 (EPSG:4326) by specification.
    """
    for driver in ("LIBKML", "KML"):
        try:
            layers = [
                (layer, gpd.read_file(kml_path, driver=driver, layer=layer))
                for layer in _list_layers(kml_path)
            ]
            features = _extract_features_from_layers(layers)
            logger.info("Read KML with GeoPandas %s driver.", driver)
            return {"crs": "EPSG:4326", "features": features}
        except Exception as e:
            logger.warning("GeoPandas %s driver failed: %s", driver, e)

    logger.info("Falling back to XML-based KML parser.")
    return _read_kml_xml(kml_path)


def _list_layers(file_path: str) -> list[str]:
    """Return the names of all layers in a multi-layer source such as KML."""
    return [str(name) for name, _geom_type in pyogrio.list_layers(file_path)]


# ---------------------------------------------------------------------------
# GeoDataFrame -> feature dicts
# ---------------------------------------------------------------------------

def _crs_to_string(crs) -> str | None:
    """Convert a GeoDataFrame CRS to an 'AUTHORITY:CODE' string (or WKT)."""
    if crs is None:
        return None
    crs_obj = CRS(crs)
    authority = crs_obj.to_authority()
    if authority:
        return f"{authority[0]}:{authority[1]}"
    return crs_obj.to_wkt()


def _extract_features_from_layers(
    layers: list[tuple[str, gpd.GeoDataFrame]],
) -> list[dict]:
    """Extract features from several layers, keeping a file-wide index.

    When there is more than one layer, each feature records its layer
    name under the 'layer' property (unless it already has one).
    """
    multi_layer = len(layers) > 1
    features: list[dict] = []
    offset = 0
    for layer_name, gdf in layers:
        for feature in _extract_features(gdf, start_index=offset):
            if multi_layer:
                feature["properties"].setdefault("layer", layer_name)
            features.append(feature)
        offset += len(gdf)
    return features


def _extract_features(gdf: gpd.GeoDataFrame, start_index: int = 0) -> list[dict]:
    """Convert GeoDataFrame rows into feature dicts.

    Features with null/empty geometry are skipped, but the index of every
    returned feature still reflects its position in the source file.
    """
    features: list[dict] = []
    prop_columns = [
        col for col in gdf.columns
        if col != gdf.geometry.name and col not in KML_STYLE_FIELDS
    ]

    for position, (_, row) in enumerate(gdf.iterrows()):
        index = start_index + position
        geom = row.geometry

        if geom is None or geom.is_empty:
            logger.warning("Feature %d has no geometry - skipping.", index)
            continue

        properties = {}
        for col in prop_columns:
            value = _to_json_value(row[col])
            if value is not None:
                properties[col] = value

        features.append({
            "index": index,
            "geometry_type": geom.geom_type,
            "geometry": geom,
            "properties": properties,
        })

    logger.info("Extracted %d features from layer.", len(features))
    return features


def _to_json_value(value):
    """Convert a pandas/numpy cell into a JSON-serialisable value (or None)."""
    if value is None:
        return None
    try:
        if pd.isna(value):  # NaN, NaT, pd.NA
            return None
    except (TypeError, ValueError):
        pass  # Array-like values; fall through
    if hasattr(value, "item"):  # numpy scalar -> Python scalar
        value = value.item()
    if hasattr(value, "isoformat"):  # dates / timestamps
        return value.isoformat()
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


# ---------------------------------------------------------------------------
# XML-based KML Fallback Reader
# ---------------------------------------------------------------------------

def _read_kml_xml(kml_path: str) -> dict:
    """Read KML features by parsing XML directly with lxml.

    This fallback is used when GeoPandas' KML/LIBKML drivers are unavailable.
    KML always uses EPSG:4326 (WGS 84) coordinates.
    """
    from lxml import etree

    # resolve_entities/no_network guard against XXE in untrusted uploads
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    root = etree.parse(kml_path, parser).getroot()
    ns = root.nsmap.get(None, KML_NS)

    features: list[dict] = []
    for index, placemark in enumerate(root.iter(f"{{{ns}}}Placemark")):
        geom = _parse_kml_geometry(placemark, ns)
        if geom is None or geom.is_empty:
            logger.warning("Placemark %d has no parseable geometry - skipping.", index)
            continue
        features.append({
            "index": index,
            "geometry_type": geom.geom_type,
            "geometry": geom,
            "properties": _parse_kml_properties(placemark, ns),
        })

    logger.info("XML parser: read %d features from KML.", len(features))
    return {"crs": "EPSG:4326", "features": features}


def _parse_kml_properties(placemark, ns: str) -> dict:
    """Extract name, description and ExtendedData from a Placemark."""
    props: dict = {}

    for tag in ("name", "description"):
        elem = placemark.find(f"{{{ns}}}{tag}")
        if elem is not None and elem.text:
            props[tag] = elem.text.strip()

    ext_data = placemark.find(f"{{{ns}}}ExtendedData")
    if ext_data is None:
        return props

    for data_elem in ext_data.findall(f"{{{ns}}}Data"):
        key = data_elem.get("name")
        value_elem = data_elem.find(f"{{{ns}}}value")
        if key and value_elem is not None and value_elem.text:
            props[key] = value_elem.text.strip()

    for simple in ext_data.iter(f"{{{ns}}}SimpleData"):
        key = simple.get("name")
        if key and simple.text:
            props[key] = simple.text.strip()

    return props


def _parse_kml_geometry(placemark, ns: str):
    """Parse the first geometry element found directly under a Placemark."""
    for child in placemark:
        parser = _geometry_parser_for(child)
        if parser:
            return parser(child, ns)
    return None


def _geometry_parser_for(elem):
    """Return the parser for a KML geometry element, or None."""
    if not isinstance(elem.tag, str):
        return None  # Comments / processing instructions
    return _KML_GEOMETRY_PARSERS.get(elem.tag.split("}")[-1])


def _coords_of(elem, ns: str) -> list[tuple[float, float]]:
    """Return the (lon, lat) pairs of an element's <coordinates> child."""
    coords_elem = elem.find(f"{{{ns}}}coordinates")
    if coords_elem is None or not coords_elem.text:
        return []
    return _parse_kml_coords(coords_elem.text)


def _parse_point(elem, ns: str):
    coords = _coords_of(elem, ns)
    return Point(coords[0]) if coords else None


def _parse_linestring(elem, ns: str):
    coords = _coords_of(elem, ns)
    return LineString(coords) if len(coords) >= 2 else None


def _parse_linear_ring(elem, ns: str):
    coords = _coords_of(elem, ns)
    return LinearRing(coords) if len(coords) >= 3 else None


def _parse_polygon(elem, ns: str):
    outer = elem.find(f"{{{ns}}}outerBoundaryIs/{{{ns}}}LinearRing")
    exterior = _coords_of(outer, ns) if outer is not None else []
    if len(exterior) < 3:
        return None
    holes = [
        hole for hole in (
            _coords_of(ring, ns)
            for ring in elem.findall(f"{{{ns}}}innerBoundaryIs/{{{ns}}}LinearRing")
        )
        if len(hole) >= 3
    ]
    return Polygon(exterior, holes or None)


def _parse_multigeometry(elem, ns: str):
    """Combine all child geometries into the most specific multi-type."""
    parts = []
    for child in elem:
        parser = _geometry_parser_for(child)
        geom = parser(child, ns) if parser else None
        if geom is not None and not geom.is_empty:
            parts.append(geom)

    if not parts:
        return None
    types = {part.geom_type for part in parts}
    if types == {"Point"}:
        return MultiPoint(parts)
    if types == {"LineString"}:
        return MultiLineString(parts)
    if types == {"Polygon"}:
        return MultiPolygon(parts)
    return GeometryCollection(parts)


_KML_GEOMETRY_PARSERS = {
    "Point": _parse_point,
    "LineString": _parse_linestring,
    "LinearRing": _parse_linear_ring,
    "Polygon": _parse_polygon,
    "MultiGeometry": _parse_multigeometry,
}


def _parse_kml_coords(coords_text: str) -> list[tuple[float, float]]:
    """Parse a KML coordinate string into (lon, lat) tuples (altitude dropped)."""
    coords: list[tuple[float, float]] = []
    for token in coords_text.split():
        parts = token.split(",")
        if len(parts) >= 2:
            try:
                coords.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue
    return coords
