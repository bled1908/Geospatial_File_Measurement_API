"""Tests for file upload functionality."""

import time

import pytest


class TestFileUpload:
    """Test suite for POST /api/files/ endpoint."""

    def test_upload_kml_file(self, client, sample_kml_path):
        """Test uploading a valid KML file."""
        with open(sample_kml_path, "rb") as f:
            response = client.post(
                "/api/files/",
                files={"file": ("sample.kml", f, "application/vnd.google-earth.kml+xml")},
            )

        assert response.status_code == 201
        data = response.json()
        assert data["filename"] == "sample.kml"
        assert data["file_type"] == "kml"
        assert data["status"] in ("PENDING", "PROCESSING", "COMPLETED")
        assert "id" in data

    def test_upload_shapefile_zip(self, client, sample_shapefile_zip):
        """Test uploading a valid Shapefile ZIP."""
        with open(sample_shapefile_zip, "rb") as f:
            response = client.post(
                "/api/files/",
                files={"file": ("test_shapefile.zip", f, "application/zip")},
            )

        assert response.status_code == 201
        data = response.json()
        assert data["filename"] == "test_shapefile.zip"
        assert data["file_type"] == "shapefile"
        assert data["status"] in ("PENDING", "PROCESSING", "COMPLETED")

    def test_upload_invalid_file_type(self, client, tmp_path):
        """Test that uploading an unsupported file type returns 400."""
        # Create a dummy .txt file
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("Not a geospatial file")

        with open(txt_file, "rb") as f:
            response = client.post(
                "/api/files/",
                files={"file": ("test.txt", f, "text/plain")},
            )

        assert response.status_code == 400
        assert "Unsupported file type" in response.json()["detail"]

    def test_upload_invalid_extension_csv(self, client, tmp_path):
        """Test that a .csv file is rejected."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("lat,lon\n12.9,77.5")

        with open(csv_file, "rb") as f:
            response = client.post(
                "/api/files/",
                files={"file": ("data.csv", f, "text/csv")},
            )

        assert response.status_code == 400


class TestFileInfo:
    """Test suite for GET /api/files/{id}/ endpoint."""

    def test_get_file_info(self, client, sample_kml_path):
        """Test retrieving file information after upload."""
        # Upload first
        with open(sample_kml_path, "rb") as f:
            upload_response = client.post(
                "/api/files/",
                files={"file": ("sample.kml", f, "application/vnd.google-earth.kml+xml")},
            )
        file_id = upload_response.json()["id"]

        # Wait briefly for background processing
        time.sleep(2)

        # Get file info
        response = client.get(f"/api/files/{file_id}/")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == file_id
        assert data["filename"] == "sample.kml"
        assert data["status"] == "COMPLETED"
        assert data["feature_count"] > 0
        assert data["crs"] is not None

    def test_get_file_info_not_found(self, client):
        """Test that a non-existent file ID returns 404."""
        response = client.get("/api/files/nonexistent-id/")
        assert response.status_code == 404

    def test_list_files(self, client, sample_kml_path):
        """Test listing all uploaded files."""
        # Upload a file first
        with open(sample_kml_path, "rb") as f:
            client.post(
                "/api/files/",
                files={"file": ("sample.kml", f, "application/vnd.google-earth.kml+xml")},
            )

        response = client.get("/api/files/")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
