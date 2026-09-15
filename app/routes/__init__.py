from fastapi import APIRouter

from app.routes.runs import router as runs_router

router = APIRouter()
router.include_router(runs_router)