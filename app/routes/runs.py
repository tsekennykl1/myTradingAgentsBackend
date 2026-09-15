from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, JSONResponse

from app.artifacts import (
    get_market_data_json_content,
    get_price_chart_json_content,
    get_price_chart_png_bytes,
    get_result_html_content,
)
from app.schemas.chart import ChartResponse
from app.services.chart_service import build_chart_response

router = APIRouter()


@router.get("/engine")
def engine_status() -> Dict[str, Any]:
    from app.services.analyze_service import engine_available

    return {"available": engine_available()}


@router.get("/runs")
def list_runs_route() -> JSONResponse:
    from app.services.analyze_service import list_runs

    return JSONResponse(content=list_runs())


@router.post("/runs")
def create_run_route(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    try:
        from app.services.analyze_service import create_or_reuse_run, get_run

        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Payload must be a JSON object.")

        result = create_or_reuse_run(payload)
        run = get_run(result["run_id"])
        if not run:
            raise HTTPException(
                status_code=500,
                detail="Run was created but could not be loaded.",
            )

        return JSONResponse(
            content={
                "run_id": result["run_id"],
                "reused": result["reused"],
                "run": run,
            },
            status_code=202,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create run: {exc}") from exc


@router.get("/runs/{run_id}")
def get_run_route(run_id: str) -> JSONResponse:
    from app.services.analyze_service import get_run

    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    return JSONResponse(content=run)


@router.post("/runs/{run_id}/cancel")
def cancel_run_route(run_id: str) -> JSONResponse:
    from app.services.analyze_service import cancel_run, get_run

    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")

    ok = cancel_run(run_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to cancel run.")

    updated = get_run(run_id)
    return JSONResponse(
        content={
            "ok": True,
            "run_id": run_id,
            "status": updated["status"] if updated else run["status"],
            "cancel_requested": True,
        }
    )


@router.get("/runs/{run_id}/decision")
def decision_route(run_id: str) -> JSONResponse:
    from app.services.analyze_service import get_run, get_run_decision

    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")

    decision = get_run_decision(run_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="Decision not available for this run.")

    return JSONResponse(content=decision)


@router.get("/runs/{run_id}/reports")
def reports_route(run_id: str) -> JSONResponse:
    from app.services.analyze_service import get_run, get_run_reports

    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")

    reports = get_run_reports(run_id)
    if reports is None:
        raise HTTPException(status_code=404, detail="Reports not available for this run.")

    return JSONResponse(content=reports)


@router.get("/runs/{run_id}/reports/{report_name}")
def report_route(run_id: str, report_name: str) -> JSONResponse:
    from app.services.analyze_service import get_run, get_run_report

    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")

    report = get_run_report(run_id, report_name)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Report '{report_name}' not found.")

    return JSONResponse(content=report)


@router.get("/runs/{run_id}/market-data")
def market_data_route(run_id: str) -> Response:
    from app.services.analyze_service import get_run

    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")

    content = get_market_data_json_content(run_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Market data artifact not found.")

    return Response(content=content, media_type="application/json")


@router.get("/runs/{run_id}/market-data.json")
def market_data_json_alias_route(run_id: str) -> Response:
    return market_data_route(run_id)


@router.get("/runs/{run_id}/price-chart.json")
def price_chart_json_route(run_id: str) -> Response:
    from app.services.analyze_service import get_run

    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")

    content = get_price_chart_json_content(run_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Price chart JSON artifact not found.")

    return Response(content=content, media_type="application/json")


@router.get("/runs/{run_id}/price-chart.png")
def price_chart_png_route(run_id: str) -> Response:
    from app.services.analyze_service import get_run

    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")

    content = get_price_chart_png_bytes(run_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Price chart PNG artifact not found.")

    return Response(content=content, media_type="image/png")


@router.get("/runs/{run_id}/chart.png")
def chart_png_legacy_route(run_id: str) -> Response:
    return price_chart_png_route(run_id)


@router.get("/chart/{symbol}", response_model=ChartResponse)
def get_chart(
    symbol: str,
    range_: str = Query(default="3y", alias="range", pattern="^(6mo|1y|3y|5y|max)$"),
    interval: Literal["1d"] = Query(default="1d"),
    include_indicators: bool = Query(default=True),
    include_signals: bool = Query(default=True),
    include_analysis: bool = Query(default=True),
    run_id: Optional[str] = Query(default=None),
) -> ChartResponse:
    return build_chart_response(
        symbol=symbol,
        range_str=range_,
        interval=interval,
        include_indicators=include_indicators,
        include_signals=include_signals,
        include_analysis=include_analysis,
        run_id=run_id,
    )


@router.get("/runs/{run_id}/price-chart.html")
def price_chart_html_route(run_id: str) -> HTMLResponse:
    from app.services.analyze_service import get_run

    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")

    png_content = get_price_chart_png_bytes(run_id)
    if png_content is None:
        raise HTTPException(
            status_code=404,
            detail="Price chart HTML artifact not available because PNG chart artifact was not found.",
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Price Chart - {run_id}</title>
  <style>
    body {{
      margin: 0;
      padding: 24px;
      font-family: Arial, sans-serif;
      background: #0f172a;
      color: #e2e8f0;
    }}
    .card {{
      max-width: 1100px;
      margin: 0 auto;
      background: #111827;
      border-radius: 12px;
      padding: 24px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
    }}
    h1 {{
      margin-top: 0;
    }}
    img {{
      width: 100%;
      height: auto;
      border-radius: 8px;
      background: white;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Price Chart</h1>
    <p>Run ID: {run_id}</p>
    <img src="/runs/{run_id}/price-chart.png" alt="Price chart">
  </div>
</body>
</html>
"""
    return HTMLResponse(content=html)


@router.get("/runs/{run_id}/chart.html")
def chart_html_legacy_route(run_id: str) -> HTMLResponse:
    return price_chart_html_route(run_id)


@router.get("/runs/{run_id}/result.html")
def result_html_route(run_id: str) -> HTMLResponse:
    from app.services.analyze_service import get_run

    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")

    content = get_result_html_content(run_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Result HTML artifact not found.")

    return HTMLResponse(content=content)


@router.get("/runs/{run_id}/status")
def run_status_route(run_id: str) -> JSONResponse:
    from app.services.analyze_service import get_run

    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")

    payload = {
        "run_id": run["run_id"],
        "status": run["status"],
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "progress": run.get("progress"),
        "error": run.get("error"),
        "cancel_requested": run.get("cancel_requested"),
        "poll_after_ms": run.get("poll_after_ms"),
    }
    return JSONResponse(content=payload)


@router.get("/runs/{run_id}/logs")
def run_logs_route(run_id: str) -> JSONResponse:
    from app.services.analyze_service import get_run

    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")

    return JSONResponse(
        content={
            "run_id": run_id,
            "logs": run.get("logs", []),
        }
    )


@router.get("/runs/{run_id}/artifacts")
def run_artifacts_route(run_id: str) -> JSONResponse:
    from app.services.analyze_service import get_run

    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")

    partial = run.get("partial_result") or {}

    return JSONResponse(
        content={
            "run_id": run_id,
            "market_data_url": partial.get("market_data_url"),
            "price_chart_json_url": partial.get("price_chart_json_url"),
            "chart_urls": partial.get("chart_urls", {}),
            "price_chart_html_url": (
                f"/runs/{run_id}/price-chart.html"
                if partial.get("chart_urls", {}).get("png")
                else None
            ),
            "result_url": partial.get("result_url"),
        }
    )


@router.post("/runs/{run_id}/artifacts/regenerate")
def regenerate_run_artifacts_route(run_id: str) -> JSONResponse:
    from app.services.analyze_service import get_run, regenerate_run_artifacts

    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")

    try:
        result = regenerate_run_artifacts(run_id)
        return JSONResponse(
            content={
                "ok": True,
                "run_id": run_id,
                "result": result,
            }
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to regenerate artifacts: {exc}",
        ) from exc