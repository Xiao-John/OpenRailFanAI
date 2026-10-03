"""回归：进程性能快照只包含脱敏聚合指标。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import metrics
from app.main import app


def test_metrics_snapshot_and_endpoint_are_aggregated_and_redacted():
    metrics.record_stage("decision", 12.5)
    metrics.record_tool("ticket.query", True, 80)
    metrics.record_tool("ticket.query", False, 120)
    metrics.record_llm("sample-provider", 40, 100, 20)

    with TestClient(app) as client:
        response = client.get("/api/metrics")

    assert response.status_code == 200
    data = response.json()
    assert data["scope"] == "process"
    assert data["stages"]["decision"]["count"] >= 1
    assert data["tools"]["ticket.query"]["calls"] >= 2
    assert data["tools"]["ticket.query"]["failed"] >= 1
    assert data["llm"]["sample-provider"]["prompt_tokens"] >= 100
    serialized = response.text.lower()
    assert "api_key" not in serialized
    assert "base_url" not in serialized
    assert "message" not in serialized


if __name__ == "__main__":
    test_metrics_snapshot_and_endpoint_are_aggregated_and_redacted()
    print("[PASS] metrics snapshot and endpoint")
