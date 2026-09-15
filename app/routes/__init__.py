from fastapi import APIRouter

from app.routes.providers import router as providers_router
from app.routes.runs import router as runs_router

router = APIRouter()
router.include_router(runs_router)
router.include_router(providers_router)
