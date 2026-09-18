"""GridWise Energy Optimization Service - Main FastAPI Application."""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.config import get_settings

# Configure logging
settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan events for startup and shutdown."""
    logger.info("GridWise Energy Optimization Service starting up...")
    yield
    logger.info("GridWise Energy Optimization Service shutting down...")


app = FastAPI(
    title="GridWise Energy Optimization API",
    description="LLM-Assisted Smart Campus Energy Optimization Service for BUP CSE Fest 2026 Hackathon",
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

# Custom validation exception handler for clean error responses
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle request validation errors cleanly without exposing internals."""
    errors = exc.errors()
    error_details = []
    for err in errors:
        loc = " -> ".join(str(l) for l in err.get("loc", []))
        msg = err.get("msg", "")
        error_details.append(f"{loc}: {msg}")
    
    logger.warning(f"Request validation failed: {error_details}")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "error": "Malformed or structurally invalid request",
            "detail": "; ".join(error_details),
        },
    )


# Mount routes
app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=False,
    )
