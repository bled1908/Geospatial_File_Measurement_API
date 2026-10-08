# Geospatial File Measurement API

A backend service built with **FastAPI** that accepts geospatial files (Shapefile or KML), processes the features within them, and returns measurement information including **area** for polygons and **length** for linestrings.

All measurements are performed using projected coordinate systems (UTM) to ensure accurate metric results — never using raw latitude/longitude degrees.

---

## Table of Contents

- [Setup](#setup)
- [API Documentation](#api-documentation)
- [Architecture](#architecture)
- [Design Decisions](#design-decisions)
- [Learnings](#learnings)
- [Future Scope](#future-scope)

---

## Setup

### Prerequisites

- **Python 3.10+** (tested on 3.12 and 3.14)
- **pip** (Python package manager)

### Installation

1. **Clone the repository:**

   ```bash
   git clone https://github.com/<your-username>/Geospatial_File_Measurement_API.git
   cd Geospatial_File_Measurement_API
   ```

2. **Create a virtual environment (recommended):**

   ```bash
   python -m venv venv
   # Windows
   venv\Scripts\activate
   # macOS/Linux
   source venv/bin/activate
   ```

3. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

4. **Run the application:**

   ```bash
   uvicorn app.main:app --reload
   ```

   The API will be available at: **http://localhost:8000**

5. **Open the interactive API docs:**

   - Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)
   - ReDoc: [http://localhost:8000/redoc](http://localhost:8000/redoc)

### Running Tests

```bash
pytest tests/ -v
```

The suite has 50 tests (about 88% line coverage) covering:

- **API:** upload, file info, list, measurements, 404/422 paths, empty and unsupported uploads
- **Accuracy:** area and length checked against geodesic ground truth (`pyproj.Geod`) in both hemispheres
- **CRS:** UTM zone selection, projected Shapefiles measured in their own CRS, Web Mercator reprojected to UTM, feet-to-metres conversion, missing `.prj`
- **Formats:** multi-folder KML, KML MultiGeometry (including the lxml fallback), Shapefile ZIPs, corrupt ZIPs
- **Security:** path-traversal filenames, ZIP bombs, macOS ZIP metadata

---

## API Documentation

### Health Check

```
GET /
```

**Response:**

```json
{
    "service": "Geospatial File Measurement API",
    "version": "1.0.0",
    "status": "healthy",
    "docs": "/docs"
}
```

---

### Upload a File

```
POST /api/files/
```

Upload a geospatial file for processing. Accepts `.zip` (containing a Shapefile) or `.kml` files.

**Request:**

```bash
curl -X POST "http://localhost:8000/api/files/" \
     -F "file=@survey.kml"
```

**Response (201 Created):**

```json
{
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "filename": "survey.kml",
    "file_type": "kml",
    "status": "PENDING",
    "created_at": "2024-01-15T10:30:00.000000"
}
```

**Error Responses:** `400` for an unsupported extension or an empty file, `413` for files over 50 MB. Problems
inside the file itself (corrupt ZIP, no `.shp`, unreadable data) don't fail the upload. Instead the file is marked
`FAILED` and the reason appears in `error_message` on `GET /api/files/{id}/`.

**Example (400 Bad Request):**

```json
{
    "detail": "Unsupported file type '.csv'. Allowed types: .kml, .zip"
}
```

---

### List All Files

```
GET /api/files/
```

Returns all uploaded files, ordered by most recent first.

**Response (200 OK):**

```json
[
    {
        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "filename": "survey.kml",
        "file_type": "kml",
        "feature_count": 5,
        "crs": "EPSG:4326",
        "status": "COMPLETED",
        "error_message": null,
        "created_at": "2024-01-15T10:30:00.000000"
    }
]
```

---

### Get File Information

```
GET /api/files/{file_id}/
```

Returns detailed information about a specific uploaded file.

**Response (200 OK):**

```json
{
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "filename": "survey.kml",
    "file_type": "kml",
    "feature_count": 5,
    "crs": "EPSG:4326",
    "status": "COMPLETED",
    "error_message": null,
    "created_at": "2024-01-15T10:30:00.000000"
}
```

**Status Values:**

| Status | Description |
|---|---|
| `PENDING` | File uploaded, processing not yet started |
| `PROCESSING` | File is being processed |
| `COMPLETED` | Processing finished successfully |
| `FAILED` | Processing encountered an error |

---

### Get Measurements

```
GET /api/files/{file_id}/measurements/
```

Returns, for every feature: its index, geometry type, geometry (GeoJSON, always WGS 84
lon/lat as RFC 7946 requires), properties/attributes, measurements, and a
`measurement_error` explaining any feature that could not be measured. The file-level `crs`
is the source CRS; each measurement records the projected CRS used to compute it.

**Request:**

```bash
curl "http://localhost:8000/api/files/a1b2c3d4-e5f6-7890-abcd-ef1234567890/measurements/"
```

**Response (200 OK)**: real output for [`tests/sample_files/sample.kml`](tests/sample_files/sample.kml), trimmed to three features:

```json
{
    "file_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "filename": "sample.kml",
    "crs": "EPSG:4326",
    "feature_count": 5,
    "features": [
        {
            "feature_index": 0,
            "geometry_type": "Point",
            "geometry": {"type": "Point", "coordinates": [77.5946, 12.9716, 0.0]},
            "properties": {
                "Name": "Bangalore City Center",
                "description": "A sample point feature",
                "category": "city",
                "population": "12000000"
            },
            "measurements": []
        },
        {
            "feature_index": 1,
            "geometry_type": "LineString",
            "geometry": {
                "type": "LineString",
                "coordinates": [[77.5946, 12.9716, 0.0], [77.61, 12.98, 0.0], [77.62, 12.99, 0.0]]
            },
            "properties": {"Name": "Sample Road", "description": "A sample linestring feature", "road_type": "highway"},
            "measurements": [
                {
                    "measurement_type": "length",
                    "value": 3463.4122,
                    "unit": "meters",
                    "projected_crs": "EPSG:32643"
                }
            ]
        },
        {
            "feature_index": 2,
            "geometry_type": "Polygon",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[77.58, 12.975, 0.0], [77.595, 12.975, 0.0], [77.595, 12.985, 0.0], [77.58, 12.985, 0.0], [77.58, 12.975, 0.0]]]
            },
            "properties": {"Name": "Cubbon Park", "description": "A sample polygon feature", "type": "park", "area_approx": "120 hectares"},
            "measurements": [
                {
                    "measurement_type": "area",
                    "value": 1802470.0112,
                    "unit": "square_meters",
                    "projected_crs": "EPSG:32643"
                }
            ]
        }
    ]
}
```

**Other responses:**

| Status | When | Body |
|---|---|---|
| `202 Accepted` | File is still `PENDING`/`PROCESSING` | `{"detail": "File is still being processed. ...", "status": "PROCESSING"}` |
| `404 Not Found` | Unknown file id | `{"detail": "File not found."}` |
| `422 Unprocessable Entity` | Processing `FAILED` | `{"detail": "File processing failed: No .shp file found in the uploaded ZIP archive. ..."}` |

**Measurement Types:**

| Geometry Type | Measurement | Unit |
|---|---|---|
| Polygon / MultiPolygon | `area` | `square_meters` |
| LineString / MultiLineString | `length` | `meters` |
| Point / MultiPoint | No measurement | — |
| Other (e.g., GeometryCollection) | Skipped gracefully | — |

---

## Architecture

### Application Structure

```
app/
├── main.py              # FastAPI app initialization, lifespan, CORS
├── config.py            # Centralized settings (upload dir, DB URL, limits)
├── database.py          # SQLAlchemy engine, session factory, dependency
├── models.py            # ORM models (UploadedFile, Feature, Measurement)
├── schemas.py           # Pydantic request/response schemas
├── routers/
│   └── files.py         # API endpoints (upload, info, measurements)
├── services/
│   ├── file_processor.py  # Orchestrates the full processing pipeline
│   ├── geo_reader.py      # Reads Shapefile/KML via GeoPandas (pyogrio) + lxml fallback
│   ├── measurement.py     # CRS-aware area/length calculations
│   └── crs_utils.py       # UTM zone detection and reprojection
└── utils/
    └── file_utils.py      # File validation, saving, ZIP extraction
```

The application follows a **layered architecture** with clear separation of concerns:

- **Routers** — Handle HTTP requests, validation, and response formatting
- **Services** — Business logic for file processing, measurement, and CRS handling
- **Utils** — Reusable helper functions for file operations
- **Models/Schemas** — Data layer (ORM) and API contracts (Pydantic)

### Database Schema

Three tables capture the full data model:

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  uploaded_files   │     │     features      │     │   measurements   │
├──────────────────┤     ├──────────────────┤     ├──────────────────┤
│ id (PK, UUID)    │◄──┐ │ id (PK, auto)    │◄──┐ │ id (PK, auto)    │
│ filename         │   │ │ file_id (FK)     │   │ │ feature_id (FK)  │
│ file_path        │   └─│ feature_index    │   └─│ measurement_type │
│ file_type        │     │ geometry_type    │     │ value            │
│ crs              │     │ geometry_wkt     │     │ unit             │
│ feature_count    │     │ properties (JSON)│     │ projected_crs    │
│ status           │     │ measurement_error│     └──────────────────┘
│                  │     └──────────────────┘
│ error_message    │
│ created_at       │
└──────────────────┘
```

### File-Processing Flow

1. **Upload** — User uploads a `.zip` or `.kml` file via `POST /api/files/`
2. **Validation** — File type and size are validated immediately
3. **Storage** — File is saved to `uploads/{file_id}/`; a database record is created with `PENDING` status
4. **Background Processing** — A background task begins:
   - Status is set to `PROCESSING`
   - For `.zip` files: the archive is extracted, and the `.shp` file is located
   - For `.kml` files: the file is read directly
   - GeoPandas (pyogrio/GDAL) reads the features. For KML, **every layer is read**: GDAL exposes each `<Folder>` as a separate layer, and reading only the default layer would silently drop features. If no GDAL KML driver is available, an lxml-based parser is used instead (with XXE protection)
   - Features with null/empty geometry are skipped; every feature keeps its original position in the file as `feature_index`
5. **Feature Extraction** — Each feature's geometry, type, and properties are extracted and stored
6. **Measurement** — For each feature, measurements are calculated (see below)
7. **Completion** — Status is set to `COMPLETED` (or `FAILED` with an error message)

### Measurement Calculation Flow

1. Identify the geometry type (`Polygon`, `LineString`, `Point`, etc.)
2. For **Polygon/MultiPolygon**:
   - Determine the geometry's centroid
   - Calculate the appropriate UTM zone from the centroid's longitude
   - Reproject the geometry to UTM using pyproj
   - Calculate area using Shapely (in square meters)
3. For **LineString/MultiLineString**:
   - Same reprojection process
   - Calculate length using Shapely (in meters)
4. For **Point/MultiPoint**: No measurement needed
5. For **unsupported types** (e.g., `GeometryCollection`): Skipped gracefully — no crash, no measurement

### CRS Handling

The application uses **automatic UTM zone detection** for accurate metric measurements:

1. **Read source CRS** — From the Shapefile's `.prj` (e.g., `EPSG:4326`, `EPSG:32643`). KML is always `EPSG:4326` by specification. If a Shapefile has **no `.prj`**, WGS 84 is assumed only when all coordinates fall within lon/lat bounds; otherwise processing fails with a clear message rather than guessing
2. **Check if projected** — A *local* projected CRS (one whose area of use spans at most 30° of longitude, e.g. a UTM or State Plane zone) is used directly, converting from its linear unit to metres (so State Plane in US survey feet still yields square metres). *Global* projections such as Web Mercator (EPSG:3857) are **not** trusted, because Web Mercator overstates area about 4x at 60° latitude. They are reprojected to UTM like geographic data
3. **Auto-detect UTM zone** — For geographic CRS (degree-based):
   - Compute the geometry's centroid
   - Calculate UTM zone: `zone = floor((longitude + 180) / 6) + 1`
   - Determine hemisphere from latitude
   - Construct EPSG code: `326xx` (North) or `327xx` (South)
4. **Reproject** — Transform geometry to the target UTM CRS using pyproj
5. **Calculate** — Perform measurement on the projected geometry

This ensures that **area and length are always calculated in meters**, never in degrees.

**Accuracy:** UTM's scale factor runs from 0.9996 on a zone's central meridian to about 1.001 at its edges. For
geometries inside one zone, UTM measurements are therefore within roughly ±0.1% (length) and ±0.2% (area) of the
true ellipsoidal value. The test suite checks results against `pyproj.Geod` geodesic ground truth (within 0.3%),
including the southern hemisphere and MultiPolygons. For geometries spanning many zones, geodesic measurement is the
natural next step (see Future Scope).

### Security and Robustness

- Client-supplied filenames are reduced to a safe basename before saving (blocks `../../` path traversal)
- ZIP members with absolute or `..` paths are rejected, and decompressed bytes are counted while streaming (headers can lie), capping extraction at 500 MB (ZIP-bomb guard)
- macOS metadata (`__MACOSX/`, `._*.shp`) inside ZIPs is ignored
- The fallback KML parser disables entity resolution and network access (XXE protection)
- Upload size is limited to 50 MB; empty uploads are rejected
- A failing feature never fails the whole file; a failing file is marked `FAILED` with a readable `error_message`

---

## Design Decisions

### 1. FastAPI over Django

**Choice:** FastAPI  
**Rationale:** FastAPI provides built-in async support, automatic OpenAPI/Swagger documentation, Pydantic-based validation, and excellent performance. For a focused API service without a full admin interface, FastAPI is lighter and more appropriate than Django + DRF.

### 2. SQLite over PostgreSQL/PostGIS

**Choice:** SQLite with SQLAlchemy  
**Rationale:** Zero-configuration database that requires no additional setup. Reviewers can clone and run the project immediately without installing a database server. PostGIS would be more appropriate for production but adds significant setup complexity for an assignment.

### 3. Background Processing with FastAPI BackgroundTasks

**Choice:** FastAPI's built-in `BackgroundTasks`  
**Alternative considered:** Celery with Redis  
**Rationale:** `BackgroundTasks` is sufficient for this scope and avoids the operational complexity of running a separate Celery worker and Redis server. The API returns immediately with `PENDING` status while processing continues.

### 4. UTM Auto-Projection for Measurements

**Choice:** Automatic UTM zone detection based on geometry centroid  
**Alternative considered:** Using a single global CRS (e.g., EPSG:3857), or geodesic calculations  
**Rationale:** UTM provides highly accurate measurements for localized areas. Auto-detection from the centroid means the service works correctly for data from anywhere on Earth. EPSG:3857 (Web Mercator) has known area distortion, and geodesic calculations, while accurate, are more complex to implement.

### 5. GeoPandas (pyogrio) + lxml Fallback for KML

**Choice:** Read all layers with GDAL's LIBKML driver, then the KML driver, then fall back to XML parsing with lxml  
**Rationale:** GeoPandas/pyogrio (GDAL) provides the most reliable reading for Shapefiles and most KML files. However, the KML driver availability depends on the GDAL build. The lxml fallback ensures KML files can always be read, even without GDAL KML support.

### 6. Storing Geometry as WKT

**Choice:** Store geometry as Well-Known Text (WKT) in the database  
**Alternative considered:** GeoJSON, WKB (Well-Known Binary)  
**Rationale:** WKT is human-readable (useful for debugging), widely supported, and sufficient for this application. WKB would be more space-efficient for production use.

### 7. File Storage on Local Filesystem

**Choice:** Store uploaded files in `uploads/{file_id}/`  
**Alternative considered:** Cloud storage (S3), database BLOBs  
**Rationale:** Simple and appropriate for the assignment scope. The `file_id` directory provides natural isolation between uploads.

---

## Learnings

1. **CRS matters a lot** — Directly calculating area using latitude/longitude gives results in "square degrees," which are meaningless. Understanding the difference between geographic (degree-based) and projected (meter-based) coordinate systems was crucial.

2. **UTM is not one CRS** — UTM is a system of 60 zones. Each zone covers 6° of longitude. Choosing the correct zone for each geometry ensures minimal distortion in measurements.

3. **KML is always WGS 84** — Unlike Shapefiles which can have any CRS, KML files are defined to always use EPSG:4326 (WGS 84) coordinates. This simplifies CRS detection for KML.

4. **Shapefile is not a single file** — A "Shapefile" is actually a collection of files (`.shp`, `.shx`, `.dbf`, `.prj`, etc.). The `.prj` file contains the CRS information. GDAL handles this complexity internally, but the upload must be a ZIP containing all of them.

5. **Graceful error handling is essential** — Geospatial data is messy. Features can have null geometries, unsupported types, or invalid coordinates. The service must handle all these cases without crashing.

6. **Background processing improves UX** — Large geospatial files can take time to process. By using background tasks, the API responds immediately and the client can poll for completion status.

7. **GDAL's driver availability varies** — The KML/LIBKML driver may not be available in all GDAL builds. Building a fallback parser ensures robustness across environments.

8. **KML folders are layers** — GDAL exposes each KML `<Folder>` as a separate layer, and `read_file` reads only the first one by default. A real-world KML can silently lose most of its features unless every layer is read.

9. **"Projected" doesn't mean "metres"** — Many State Plane CRSs use US survey feet. Checking `is_projected` alone would report square feet labelled as square metres, so the CRS's axis unit has to be applied.

---

## Future Scope

1. **PostgreSQL + PostGIS** — Migrate to PostGIS for native spatial indexing, spatial queries (e.g., "find all features within a bounding box"), and better performance at scale.

2. **Celery Task Queue** — Replace `BackgroundTasks` with Celery + Redis for more robust async processing with retry logic, progress tracking, and scalability across multiple workers.

3. **Cloud Storage (S3)** — Store uploaded files in Amazon S3 or similar object storage for scalability, durability, and CDN support.

4. **GeoJSON/GeoPackage Support** — Extend file format support to include GeoJSON, GeoPackage, and other common formats.

5. **Spatial Queries API** — Add endpoints for spatial operations: intersection, buffering, bounding box queries, nearest-neighbor search.

6. **WebSocket Progress Updates** — Push real-time processing progress to clients via WebSockets instead of requiring polling.

7. **Docker + Docker Compose** — Containerize the application for consistent deployment across environments.

8. **Authentication & Rate Limiting** — Add API key authentication and rate limiting for production deployment.

9. **Geodesic Measurements** — For geometries spanning multiple UTM zones, use geodesic calculations (e.g., via `pyproj.Geod`) for even higher accuracy.

10. **File Preview** — Add an endpoint that returns a GeoJSON representation of the features for map visualization.

11. **Batch Upload** — Support uploading multiple files in a single request.

12. **Measurement Unit Conversion** — Allow users to request measurements in different units (square kilometers, miles, acres, hectares, etc.).
