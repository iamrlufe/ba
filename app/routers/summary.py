"""GET /api/summary/daily -- on-demand daily alert/job-status summary.

Reuses `app.workers.daily_summary.build_daily_summary`, the same
read-only assembly function the background worker logs once a day; this
endpoint always computes a fresh snapshot on demand, regardless of the
worker's own once-a-day schedule.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.schemas.summary import DailySummary
from app.workers.daily_summary import build_daily_summary

router = APIRouter(tags=["summary"])


@router.get("/daily", response_model=DailySummary, dependencies=[Depends(get_current_user)])
async def get_daily_summary(session: AsyncSession = Depends(get_db)) -> DailySummary:
    return await build_daily_summary(session)
