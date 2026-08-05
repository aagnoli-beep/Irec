def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready(client):
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_correlation_id_echoed(client):
    response = client.get("/health", headers={"x-correlation-id": "corr-42"})
    assert response.headers["x-correlation-id"] == "corr-42"


def test_correlation_id_generated_when_absent(client):
    response = client.get("/health")
    assert response.headers["x-correlation-id"]
