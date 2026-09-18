"""API routes for GridWise Energy Optimization service."""

import logging
from typing import Annotated, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from app.schemas.request import OptimizeRequest
from app.schemas.response import OptimizeResponse, HealthResponse, ErrorResponse
from app.services.optimize_service import OptimizeService
from app.llm.provider import get_mistral_provider
from app.llm.interpreter import build_interpreter
from app.llm.base import LLMProvider, LLMError
from app.optimization.solver import OptimizationError
from app.validation.replay import ValidationError
from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter()


def get_llm_provider() -> Optional[LLMProvider]:
    """Shared Mistral provider, or None when no key is configured.

    Never raises: request validation (400) must come before any LLM concern, and a
    missing/broken key must not turn valid requests into 503s (the interpreter falls
    back to the emergency parser and logs the problem).
    """
    try:
        return get_mistral_provider(get_settings().llm_model)
    except LLMError as e:
        logger.error(f"LLM provider unavailable: {e}")
        return None


def get_optimize_service(
    llm_provider: Annotated[Optional[LLMProvider], Depends(get_llm_provider)],
) -> OptimizeService:
    """Dependency provider for OptimizeService."""
    return OptimizeService(interpreter=build_interpreter(llm_provider))


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint required by challenge spec."""
    return HealthResponse(status="ok")


@router.post(
    "/optimize-energy",
    response_model=OptimizeResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Malformed JSON or structurally invalid request"},
        422: {"model": ErrorResponse, "description": "Unprocessable Entity or infeasible constraints"},
        500: {"model": ErrorResponse, "description": "Controlled internal error"},
        503: {"model": ErrorResponse, "description": "Service Unavailable"},
    },
)
async def optimize_energy(
    request: OptimizeRequest,
    service: Annotated[OptimizeService, Depends(get_optimize_service)],
):
    """
    Main energy optimization endpoint.
    Interprets operator notes, validates directives, runs LP solver, and verifies schedule.
    """
    try:
        response = await service.optimize(request)
        return response
    except OptimizationError as e:
        logger.warning(f"Optimization infeasible or failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Optimization failed: {str(e)}",
        )
    except ValidationError as e:
        logger.error(f"Post-solve validation error: {e}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation failed: {str(e)}",
        )
    except LLMError as e:
        logger.error(f"LLM processing error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="LLM processing error occurred while interpreting operator notes.",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error occurred during energy optimization")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected internal error occurred.",
        )
