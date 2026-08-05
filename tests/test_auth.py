import time


def test_valid_token(client, make_token):
    response = client.get(
        "/v1/_whoami", headers={"Authorization": f"Bearer {make_token()}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == "tenant-abc"
    assert body["entitlement"] == "irec:pro"


def test_missing_token(client):
    response = client.get("/v1/_whoami")
    assert response.status_code == 401
    assert response.json()["code"] == "missing_token"


def test_expired_token(client, make_token):
    token = make_token(exp=int(time.time()) - 10)
    response = client.get("/v1/_whoami", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["code"] == "token_expired"


def test_wrong_audience(client, make_token):
    token = make_token(aud="other-service")
    response = client.get("/v1/_whoami", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_audience"


def test_missing_entitlement(client, make_token):
    token = make_token(entitlement=None)
    response = client.get("/v1/_whoami", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    assert response.json()["code"] == "entitlement_missing"


def test_missing_tenant(client, make_token):
    token = make_token(tenant_id=None)
    response = client.get("/v1/_whoami", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["code"] == "missing_tenant"


def test_garbage_token(client):
    response = client.get("/v1/_whoami", headers={"Authorization": "Bearer not.a.jwt"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_error_shape_matches_contract(client):
    """Gli errori rispettano il formato {error, code} di openapi.yaml."""
    response = client.get("/v1/_whoami")
    body = response.json()
    assert set(body.keys()) == {"error", "code"}
