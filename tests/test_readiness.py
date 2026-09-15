import os
from pathlib import Path

from fastapi.testclient import TestClient


def test_status_is_lightweight_and_conditional(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_USE_MOCK", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    import app.services.analyze_service as service
    service.DB_PATH = tmp_path / "app.db"
    service._init_db()
    now = service._utc_now()
    run_id = "status-test"
    run = {"run_id":run_id,"status":"queued","payload":{"ticker":"0700.HK"},"created_at":now,"updated_at":now,"started_at":None,"completed_at":None,"error":None,"result":None,"partial_result":{},"cancel_requested":False,"logs":[],"poll_after_ms":1000,"capabilities":{},"progress":{},"timings":{}}
    service._insert_run(run, "status-hash")
    from app.main import app
    with TestClient(app) as client:
        first = client.get(f"/runs/{run_id}/status")
        assert first.status_code == 200
        body = first.json()
        assert {"version", "decision_ready", "reports_ready", "result_url"} <= body.keys()
        assert "logs" not in body and "result" not in body
        second = client.get(f"/runs/{run_id}/status", headers={"If-None-Match": first.headers["etag"]})
        assert second.status_code == 304


def test_progressive_report_publication(tmp_path):
    import app.services.analyze_service as service
    service.DB_PATH = tmp_path / "app.db"
    service._init_db()
    now = service._utc_now()
    run = {"run_id":"r1","status":"queued","payload":{"ticker":"0700.HK"},"created_at":now,"updated_at":now,"started_at":None,"completed_at":None,"error":None,"result":None,"partial_result":{},"cancel_requested":False,"logs":[],"poll_after_ms":1000,"capabilities":{},"progress":{},"timings":{}}
    service._insert_run(run, "hash")
    assert service.publish_report("r1", "market_report", "ready")
    status = service.get_run_status("r1")
    assert status["reports_ready"] == ["market_report"]
    assert service.get_run_report("r1", "market_report") == "ready"
