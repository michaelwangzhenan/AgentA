"""API 健康检查端点 UT"""

from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "version" in data
