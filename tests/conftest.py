"""Pytest configuration and shared fixtures."""

import os
import shutil
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def test_db(tmp_path):
    """Create a fresh SQLite database for each test.

    Returns the TestingSessionLocal factory so that the client fixture
    can also override SessionLocal on the database module (used by
    background tasks).
    """
    db_path = tmp_path / "test.db"
    db_url = f"sqlite:///{db_path}"

    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    yield TestingSessionLocal  # yield the session factory

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(test_db, tmp_path, monkeypatch):
    """Create a FastAPI test client with a clean database and upload dir.

    Monkeypatches both config.UPLOAD_DIR and database.SessionLocal
    so that the entire application (including background tasks) uses
    the test database and a temporary upload directory.
    """
    import app.config as config_module
    import app.database as db_module

    # Override upload directory
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(config_module, "UPLOAD_DIR", upload_dir)

    # Override SessionLocal so background tasks use the test DB
    monkeypatch.setattr(db_module, "SessionLocal", test_db)

    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Sample file fixtures
# ---------------------------------------------------------------------------

SAMPLE_DIR = Path(__file__).parent / "sample_files"


@pytest.fixture
def sample_kml_path():
    """Return the path to the sample KML file."""
    return SAMPLE_DIR / "sample.kml"


@pytest.fixture
def sample_shapefile_zip(tmp_path):
    """Create a sample Shapefile ZIP with Polygon features.

    Uses GeoPandas to generate a real Shapefile, then zips it.
    """
    import geopandas as gpd
    from shapely.geometry import Polygon

    shp_dir = tmp_path / "shapefile"
    shp_dir.mkdir()

    # Create polygons
    polygons = [
        Polygon([
            (77.58, 12.975),
            (77.595, 12.975),
            (77.595, 12.985),
            (77.58, 12.985),
            (77.58, 12.975),
        ]),
        Polygon([
            (77.585, 12.950),
            (77.595, 12.950),
            (77.595, 12.960),
            (77.585, 12.960),
            (77.585, 12.950),
        ]),
    ]

    gdf = gpd.GeoDataFrame(
        {
            "name": ["Test Park", "Test Area"],
            "category": ["park", "garden"],
        },
        geometry=polygons,
        crs="EPSG:4326",
    )

    shp_path = str(shp_dir / "test_polygons.shp")
    gdf.to_file(shp_path, driver="ESRI Shapefile")

    # Create ZIP
    zip_path = tmp_path / "test_shapefile.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in shp_dir.glob("test_polygons.*"):
            z.write(f, f.name)

    return zip_path
