"""FastAPI application entry point.

This module creates and configures the FastAPI application,
including database initialization, CORS middleware, and router registration.

Usage:
    uvicorn app.main:app --reload
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.config as config
from app.database import engine, Base
from app.routers.files import router as files_router

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler.

    - Creates database tables on startup.
    - Ensures the uploads directory exists.
    """
    logger.info("Starting Geospatial File Measurement API...")
    Base.metadata.create_all(bind=engine)
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Database tables created. Upload directory: %s", config.UPLOAD_DIR)
    yield
    logger.info("Shutting down Geospatial File Measurement API.")


app = FastAPI(
    title="Geospatial File Measurement API",
    description=(
        "A backend service that accepts geospatial files (Shapefile or KML), "
        "processes the features within them, and returns measurement information "
        "including area for polygons and length for linestrings. "
        "All measurements use projected coordinate systems (UTM) for accuracy."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(files_router)


@app.get("/", tags=["Health"])
def root():
    """Health check endpoint."""
    return {
        "service": "Geospatial File Measurement API",
        "version": "1.0.0",
        "status": "healthy",
        "docs": "/docs",
    }
