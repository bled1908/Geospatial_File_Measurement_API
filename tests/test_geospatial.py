"""Unit and integration tests for CRS handling, measurement accuracy and
edge cases (multi-layer KML, MultiGeometry, missing .prj, unsafe uploads)."""

import time
import zipfile

import geopandas as gpd
import pytest
from pyproj import CRS, Geod
from shapely.geometry import (
    GeometryCollection,
    LineString,
    Point,
    Polygon,
    box,
)

from app.services import geo_reader
from app.services.crs_utils import (
    get_projected_crs_for_geometry,
    get_utm_crs,
    reproject_geometry,
)
from app.services.measurement import calculate_measurements
from app.utils.file_utils import safe_filename

WGS84 = CRS.from_epsg(4326)
GEOD = Geod(ellps="WGS84")

# UTM's scale factor varies from 0.9996 on the central meridian to ~1.0010 at
# zone edges, so UTM areas can differ from true ellipsoidal areas by up to
# ~0.2%. Results are checked against geodesic ground truth within 0.3%.
UTM_TOLERANCE = 3e-3

MULTI_FOLDER_KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Folder><name>Points</name>
    <Placemark><name>p1</name><Point><coordinates>77.59,12.97,0</coordinates></Point></Placemark>
  </Folder>
  <Folder><name>Parcels</name>
    <Placemark><name>two-parcels</name><MultiGeometry>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>
        77.58,12.975 77.595,12.975 77.595,12.985 77.58,12.985 77.58,12.975
      </coordinates></LinearRing></outerBoundaryIs></Polygon>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>
        77.60,12.975 77.615,12.975 77.615,12.985 77.60,12.985 77.60,12.975
      </coordinates></LinearRing></outerBoundaryIs></Polygon>
    </MultiGeometry></Placemark>
  </Folder>
</Document></kml>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _geodesic_area(polygon: Polygon) -> float:
    """Ground-truth ellipsoidal area in m^2 for a lon/lat polygon."""
    area, _perimeter = GEOD.geometry_area_perimeter(polygon)
    return abs(area)


def _zip_shapefile(gdf: gpd.GeoDataFrame, tmp_path, drop_prj: bool = False):
    shp_dir = tmp_path / "shp"
    shp_dir.mkdir(exist_ok=True)
    gdf.to_file(shp_dir / "data.shp", driver="ESRI Shapefile")
    zip_path = tmp_path / "data.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        for f in shp_dir.glob("data.*"):
            if drop_prj and f.suffix == ".prj":
                continue
            z.write(f, f.name)
    return zip_path


def _upload_and_wait(client, path, filename):
    with open(path, "rb") as f:
        response = client.post("/api/files/", files={"file": (filename, f)})
    assert response.status_code == 201, response.text
    file_id = response.json()["id"]
    for _ in range(40):
        info = client.get(f"/api/files/{file_id}/").json()
        if info["status"] in ("COMPLETED", "FAILED"):
            return file_id, info
        time.sleep(0.25)
    pytest.fail("Processing did not finish in time")


# ---------------------------------------------------------------------------
# CRS selection
# ---------------------------------------------------------------------------

class TestUtmSelection:

    @pytest.mark.parametrize(
        ("lon", "lat", "expected_epsg"),
        [
            (77.59, 12.97, 32643),    # Bangalore -> zone 43N
            (151.21, -33.87, 32756),  # Sydney -> zone 56S
            (-74.00, 40.71, 32618),   # New York -> zone 18N
            (180.0, 0.0, 32660),      # Antimeridian clamps to zone 60
        ],
    )
    def test_utm_zone_from_lon_lat(self, lon, lat, expected_epsg):
        assert get_utm_crs(lon, lat).to_epsg() == expected_epsg

    def test_geographic_source_uses_utm(self):
        crs = get_projected_crs_for_geometry(Point(77.59, 12.97), WGS84)
        assert crs.to_epsg() == 32643

    def test_projected_source_is_used_directly(self):
        utm = CRS.from_epsg(32643)
        assert get_projected_crs_for_geometry(Point(500000, 1400000), utm) == utm


# ---------------------------------------------------------------------------
# Measurement accuracy
# ---------------------------------------------------------------------------

class TestMeasurementAccuracy:

    def test_polygon_area_matches_geodesic_area(self):
        polygon = box(77.58, 12.975, 77.595, 12.985)
        [m] = calculate_measurements(polygon, "Polygon", WGS84)

        expected = _geodesic_area(polygon)
        assert m["measurement_type"] == "area"
        assert m["unit"] == "square_meters"
        assert m["value"] == pytest.approx(expected, rel=UTM_TOLERANCE)

    def test_line_length_matches_geodesic_length(self):
        line = LineString([(77.5946, 12.9716), (77.61, 12.98), (77.62, 12.99)])
        [m] = calculate_measurements(line, "LineString", WGS84)

        expected = GEOD.geometry_length(line)
        assert m["measurement_type"] == "length"
        assert m["value"] == pytest.approx(expected, rel=UTM_TOLERANCE)

    def test_southern_hemisphere_area(self):
        polygon = box(151.20, -33.88, 151.22, -33.86)
        [m] = calculate_measurements(polygon, "Polygon", WGS84)
        assert m["projected_crs"] == "EPSG:32756"
        assert m["value"] == pytest.approx(_geodesic_area(polygon), rel=UTM_TOLERANCE)

    def test_area_is_not_computed_in_degrees(self):
        polygon = box(77.58, 12.975, 77.595, 12.985)
        [m] = calculate_measurements(polygon, "Polygon", WGS84)
        assert m["value"] > 1_000_000 * polygon.area  # degrees^2 would be tiny

    def test_projected_crs_in_feet_is_converted_to_metres(self):
        # EPSG:2249 = NAD83 / Massachusetts Mainland in US survey feet
        square_1000ft = box(0, 0, 1000, 1000)
        [m] = calculate_measurements(square_1000ft, "Polygon", CRS.from_epsg(2249))

        us_survey_foot = 1200 / 3937
        assert m["value"] == pytest.approx((1000 * us_survey_foot) ** 2, rel=1e-6)
        assert m["projected_crs"] == "EPSG:2249"

    def test_web_mercator_source_is_reprojected_to_utm(self):
        # Web Mercator inflates area ~4x at 60N; it must not be measured directly
        lonlat_box = box(25.00, 60.00, 25.01, 60.01)
        mercator_box = reproject_geometry(lonlat_box, WGS84, CRS.from_epsg(3857))

        [m] = calculate_measurements(mercator_box, "Polygon", CRS.from_epsg(3857))
        assert m["projected_crs"] == "EPSG:32635"
        assert m["value"] == pytest.approx(_geodesic_area(lonlat_box), rel=UTM_TOLERANCE)

    def test_multipolygon_area_is_sum_of_parts(self):
        a = box(77.58, 12.975, 77.595, 12.985)
        b = box(77.60, 12.975, 77.615, 12.985)
        [m] = calculate_measurements(a.union(b), "MultiPolygon", WGS84)
        assert m["value"] == pytest.approx(
            _geodesic_area(a) + _geodesic_area(b), rel=UTM_TOLERANCE
        )

    def test_point_has_no_measurement(self):
        assert calculate_measurements(Point(77.59, 12.97), "Point", WGS84) == []

    def test_unsupported_geometry_is_skipped_gracefully(self):
        collection = GeometryCollection([Point(0, 0), LineString([(0, 0), (1, 1)])])
        assert calculate_measurements(collection, "GeometryCollection", WGS84) == []


# ---------------------------------------------------------------------------
# KML reading
# ---------------------------------------------------------------------------

class TestKmlReading:

    @pytest.fixture
    def multi_folder_kml(self, tmp_path):
        path = tmp_path / "multi.kml"
        path.write_text(MULTI_FOLDER_KML, encoding="utf-8")
        return path

    def test_all_folders_are_read(self, multi_folder_kml):
        data = geo_reader.read_geospatial_file(str(multi_folder_kml), "kml")

        types = [f["geometry_type"] for f in data["features"]]
        assert types == ["Point", "MultiPolygon"]
        assert [f["index"] for f in data["features"]] == [0, 1]
        assert data["crs"] == "EPSG:4326"

    def test_xml_fallback_keeps_every_multigeometry_part(self, multi_folder_kml):
        data = geo_reader._read_kml_xml(str(multi_folder_kml))

        multi = data["features"][1]
        assert multi["geometry_type"] == "MultiPolygon"
        assert len(multi["geometry"].geoms) == 2
        assert multi["properties"]["name"] == "two-parcels"

    def test_null_and_nat_properties_are_dropped(self, multi_folder_kml):
        data = geo_reader.read_geospatial_file(str(multi_folder_kml), "kml")
        for feature in data["features"]:
            assert "NaT" not in feature["properties"].values()
            assert None not in feature["properties"].values()


# ---------------------------------------------------------------------------
# API edge cases
# ---------------------------------------------------------------------------

class TestApiEdgeCases:

    def test_measurements_include_geojson_geometry(self, client, sample_kml_path):
        file_id, _ = _upload_and_wait(client, sample_kml_path, "sample.kml")
        features = client.get(f"/api/files/{file_id}/measurements/").json()["features"]

        point = next(f for f in features if f["geometry_type"] == "Point")
        assert point["geometry"]["type"] == "Point"
        assert point["geometry"]["coordinates"][:2] == [77.5946, 12.9716]

    def test_multi_folder_kml_end_to_end(self, client, tmp_path):
        path = tmp_path / "multi.kml"
        path.write_text(MULTI_FOLDER_KML, encoding="utf-8")

        file_id, info = _upload_and_wait(client, path, "multi.kml")
        assert info["status"] == "COMPLETED"
        assert info["feature_count"] == 2

        features = client.get(f"/api/files/{file_id}/measurements/").json()["features"]
        assert features[1]["measurements"][0]["measurement_type"] == "area"

    def test_projected_shapefile_is_measured_in_its_own_crs(self, client, tmp_path):
        gdf = gpd.GeoDataFrame(
            {"name": ["1km square"]},
            geometry=[box(500000, 1400000, 501000, 1401000)],
            crs="EPSG:32643",
        )
        file_id, info = _upload_and_wait(client, _zip_shapefile(gdf, tmp_path), "utm.zip")
        assert info["crs"] == "EPSG:32643"

        [feature] = client.get(f"/api/files/{file_id}/measurements/").json()["features"]
        assert feature["measurements"][0]["value"] == pytest.approx(1_000_000)
        assert feature["measurements"][0]["projected_crs"] == "EPSG:32643"
        # GeoJSON output is converted to lon/lat even for projected sources
        lon, lat = feature["geometry"]["coordinates"][0][0][:2]
        assert 74 < lon < 76 and 12 < lat < 13

    def test_macos_metadata_in_zip_is_ignored(self, client, tmp_path):
        gdf = gpd.GeoDataFrame(
            {"name": ["park"]}, geometry=[box(77.58, 12.975, 77.595, 12.985)], crs="EPSG:4326"
        )
        zip_path = _zip_shapefile(gdf, tmp_path)
        with zipfile.ZipFile(zip_path, "a") as z:
            z.writestr("__MACOSX/._data.shp", b"resource fork junk")
            z.writestr("._data.shp", b"resource fork junk")

        _, info = _upload_and_wait(client, zip_path, "mac.zip")
        assert info["status"] == "COMPLETED", info["error_message"]

    def test_zip_bomb_is_rejected(self, client, tmp_path, monkeypatch):
        import app.config as config

        monkeypatch.setattr(config, "MAX_EXTRACTED_SIZE", 1024)
        zip_path = tmp_path / "bomb.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("data.shp", b"\0" * 10_000)

        _, info = _upload_and_wait(client, zip_path, "bomb.zip")
        assert info["status"] == "FAILED"
        assert "too large" in info["error_message"]

    def test_shapefile_without_prj_in_lon_lat_assumes_wgs84(self, client, tmp_path):
        gdf = gpd.GeoDataFrame(
            {"name": ["park"]}, geometry=[box(77.58, 12.975, 77.595, 12.985)], crs="EPSG:4326"
        )
        zip_path = _zip_shapefile(gdf, tmp_path, drop_prj=True)
        _, info = _upload_and_wait(client, zip_path, "noprj.zip")
        assert info["status"] == "COMPLETED"
        assert info["crs"] == "EPSG:4326"

    def test_shapefile_without_prj_in_metres_fails_clearly(self, client, tmp_path):
        gdf = gpd.GeoDataFrame(
            {"name": ["x"]}, geometry=[box(500000, 1400000, 501000, 1401000)], crs="EPSG:32643"
        )
        zip_path = _zip_shapefile(gdf, tmp_path, drop_prj=True)
        file_id, info = _upload_and_wait(client, zip_path, "noprj.zip")

        assert info["status"] == "FAILED"
        assert ".prj" in info["error_message"]
        assert client.get(f"/api/files/{file_id}/measurements/").status_code == 422

    def test_zip_without_shapefile_fails_gracefully(self, client, tmp_path):
        zip_path = tmp_path / "empty.zip"
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("readme.txt", "no shapefile here")

        _, info = _upload_and_wait(client, zip_path, "empty.zip")
        assert info["status"] == "FAILED"
        assert "No .shp file" in info["error_message"]

    def test_corrupt_zip_fails_gracefully(self, client, tmp_path):
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"this is not a zip archive")
        _, info = _upload_and_wait(client, bad, "bad.zip")
        assert info["status"] == "FAILED"

    def test_empty_upload_is_rejected(self, client, tmp_path):
        empty = tmp_path / "empty.kml"
        empty.write_bytes(b"")
        with open(empty, "rb") as f:
            response = client.post("/api/files/", files={"file": ("empty.kml", f)})
        assert response.status_code == 400

    def test_path_traversal_filename_is_contained(self, client, sample_kml_path):
        import app.config as config

        with open(sample_kml_path, "rb") as f:
            response = client.post(
                "/api/files/", files={"file": ("../../escape.kml", f)}
            )
        assert response.status_code == 201
        file_id = response.json()["id"]
        assert (config.UPLOAD_DIR / file_id / "escape.kml").exists()
        assert not (config.UPLOAD_DIR.parent / "escape.kml").exists()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("survey.kml", "survey.kml"),
        ("../../etc/passwd.kml", "passwd.kml"),
        ("..\\..\\win.kml", "win.kml"),
        ("my survey (1).kml", "my_survey__1_.kml"),
        ("..", "upload"),
    ],
)
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected
