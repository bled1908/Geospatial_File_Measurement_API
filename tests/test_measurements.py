"""Tests for measurement calculation and retrieval."""

import time

import pytest


class TestMeasurements:
    """Test suite for GET /api/files/{id}/measurements/ endpoint."""

    def _upload_and_wait(self, client, file_path, filename, content_type="application/octet-stream"):
        """Helper: upload a file and wait for processing to complete."""
        with open(file_path, "rb") as f:
            response = client.post(
                "/api/files/",
                files={"file": (filename, f, content_type)},
            )
        assert response.status_code == 201
        file_id = response.json()["id"]

        # Wait for background processing (with timeout)
        for _ in range(20):
            info = client.get(f"/api/files/{file_id}/").json()
            if info["status"] in ("COMPLETED", "FAILED"):
                break
            time.sleep(0.5)

        return file_id

    def test_kml_measurements(self, client, sample_kml_path):
        """Test that KML measurements are correctly calculated."""
        file_id = self._upload_and_wait(
            client, sample_kml_path, "sample.kml", "application/vnd.google-earth.kml+xml"
        )

        # Check file completed successfully
        info = client.get(f"/api/files/{file_id}/").json()
        assert info["status"] == "COMPLETED", f"Processing failed: {info.get('error_message')}"

        # Get measurements
        response = client.get(f"/api/files/{file_id}/measurements/")
        assert response.status_code == 200

        data = response.json()
        assert data["file_id"] == file_id
        assert data["feature_count"] > 0
        assert len(data["features"]) > 0

        # Verify different geometry types are present
        geometry_types = {f["geometry_type"] for f in data["features"]}
        # KML sample should have Point, LineString, and Polygon
        assert "Point" in geometry_types, f"Expected Point in {geometry_types}"
        assert "LineString" in geometry_types, f"Expected LineString in {geometry_types}"
        assert "Polygon" in geometry_types, f"Expected Polygon in {geometry_types}"

    def test_polygon_has_area_measurement(self, client, sample_kml_path):
        """Test that polygon features have area measurements in square meters."""
        file_id = self._upload_and_wait(
            client, sample_kml_path, "sample.kml", "application/vnd.google-earth.kml+xml"
        )

        response = client.get(f"/api/files/{file_id}/measurements/")
        data = response.json()

        polygon_features = [
            f for f in data["features"] if f["geometry_type"] == "Polygon"
        ]
        assert len(polygon_features) > 0, "No polygon features found"

        for pf in polygon_features:
            assert len(pf["measurements"]) > 0, "Polygon has no measurements"
            area_measurement = pf["measurements"][0]
            assert area_measurement["measurement_type"] == "area"
            assert area_measurement["unit"] == "square_meters"
            assert area_measurement["value"] > 0, "Area should be positive"
            # Area should be reasonable (not degree-based)
            # Our test polygon is roughly 1.5km x 1.1km ≈ 1,650,000 sq meters
            # Allow a wide tolerance
            assert area_measurement["value"] > 1000, (
                f"Area {area_measurement['value']} seems too small — "
                "possibly using degrees instead of meters"
            )

    def test_linestring_has_length_measurement(self, client, sample_kml_path):
        """Test that linestring features have length measurements in meters."""
        file_id = self._upload_and_wait(
            client, sample_kml_path, "sample.kml", "application/vnd.google-earth.kml+xml"
        )

        response = client.get(f"/api/files/{file_id}/measurements/")
        data = response.json()

        line_features = [
            f for f in data["features"] if f["geometry_type"] == "LineString"
        ]
        assert len(line_features) > 0, "No LineString features found"

        for lf in line_features:
            assert len(lf["measurements"]) > 0, "LineString has no measurements"
            length_measurement = lf["measurements"][0]
            assert length_measurement["measurement_type"] == "length"
            assert length_measurement["unit"] == "meters"
            assert length_measurement["value"] > 0, "Length should be positive"
            assert length_measurement["value"] > 100, (
                f"Length {length_measurement['value']} seems too small — "
                "possibly using degrees instead of meters"
            )

    def test_point_has_no_measurement(self, client, sample_kml_path):
        """Test that point features have no measurements."""
        file_id = self._upload_and_wait(
            client, sample_kml_path, "sample.kml", "application/vnd.google-earth.kml+xml"
        )

        response = client.get(f"/api/files/{file_id}/measurements/")
        data = response.json()

        point_features = [
            f for f in data["features"] if f["geometry_type"] == "Point"
        ]
        assert len(point_features) > 0, "No Point features found"

        for pf in point_features:
            assert len(pf["measurements"]) == 0, (
                "Point should have no measurements"
            )

    def test_shapefile_measurements(self, client, sample_shapefile_zip):
        """Test measurements for an uploaded Shapefile."""
        file_id = self._upload_and_wait(
            client, sample_shapefile_zip, "test_shapefile.zip", "application/zip"
        )

        info = client.get(f"/api/files/{file_id}/").json()
        assert info["status"] == "COMPLETED", f"Processing failed: {info.get('error_message')}"

        response = client.get(f"/api/files/{file_id}/measurements/")
        assert response.status_code == 200

        data = response.json()
        assert data["feature_count"] > 0

        # All features in the test shapefile are polygons
        for feature in data["features"]:
            assert feature["geometry_type"] == "Polygon"
            assert len(feature["measurements"]) > 0
            assert feature["measurements"][0]["measurement_type"] == "area"
            assert feature["measurements"][0]["value"] > 0

    def test_measurements_not_found(self, client):
        """Test that a non-existent file ID returns 404."""
        response = client.get("/api/files/nonexistent-id/measurements/")
        assert response.status_code == 404

    def test_crs_is_reported(self, client, sample_kml_path):
        """Test that the CRS is correctly reported in measurements."""
        file_id = self._upload_and_wait(
            client, sample_kml_path, "sample.kml", "application/vnd.google-earth.kml+xml"
        )

        response = client.get(f"/api/files/{file_id}/measurements/")
        data = response.json()

        assert data["crs"] is not None
        # KML is always WGS 84
        assert "4326" in data["crs"]

    def test_projected_crs_in_measurements(self, client, sample_kml_path):
        """Test that measurements include the projected CRS used."""
        file_id = self._upload_and_wait(
            client, sample_kml_path, "sample.kml", "application/vnd.google-earth.kml+xml"
        )

        response = client.get(f"/api/files/{file_id}/measurements/")
        data = response.json()

        for feature in data["features"]:
            for m in feature["measurements"]:
                assert "projected_crs" in m
                # Should be a UTM zone (EPSG:326xx or EPSG:327xx)
                assert "EPSG" in m["projected_crs"]

    def test_feature_properties_preserved(self, client, sample_kml_path):
        """Test that feature properties/attributes are preserved."""
        file_id = self._upload_and_wait(
            client, sample_kml_path, "sample.kml", "application/vnd.google-earth.kml+xml"
        )

        response = client.get(f"/api/files/{file_id}/measurements/")
        data = response.json()

        # At least some features should have properties
        features_with_props = [
            f for f in data["features"]
            if f["properties"] and len(f["properties"]) > 0
        ]
        assert len(features_with_props) > 0, "Expected some features to have properties"
