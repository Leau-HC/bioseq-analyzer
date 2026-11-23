import sqlite3

import pytest

from energie_congo import DEMO_NIU, create_app


@pytest.fixture
def app(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DATABASE": str(tmp_path / "test.db"),
        }
    )
    yield application


@pytest.fixture
def client(app):
    return app.test_client()


def csrf_token(response):
    html = response.get_data(as_text=True)
    return html.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]


def login(client):
    response = client.get("/login")
    response = client.post(
        "/login",
        data={"csrf_token": csrf_token(response), "niu": DEMO_NIU},
        follow_redirects=True,
    )
    assert "Code de démonstration".encode() in response.data
    response = client.post(
        "/verify",
        data={"csrf_token": csrf_token(response), "otp": "246810"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"FAC-DEMO-2026-001" in response.data
    return response


def test_dashboard_requires_login(client):
    response = client.get("/dashboard")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_login_requires_valid_demo_otp(client):
    response = client.get("/login")
    response = client.post(
        "/login",
        data={"csrf_token": csrf_token(response), "niu": DEMO_NIU},
    )
    response = client.get(response.headers["Location"])
    response = client.post(
        "/verify",
        data={"csrf_token": csrf_token(response), "otp": "000000"},
    )
    assert b"Code de d\xc3\xa9monstration incorrect" in response.data


def test_demo_payment_is_idempotent_and_never_marks_invoice_paid(client, app):
    login(client)
    page = client.get("/dashboard")
    payment_link = b"/payment/1"
    assert payment_link in page.data

    payment_page = client.get("/payment/1")
    token = csrf_token(payment_page)
    idempotency_key = payment_page.get_data(as_text=True).split(
        'name="idempotency_key" value="', 1
    )[1].split('"', 1)[0]
    form = {
        "csrf_token": token,
        "idempotency_key": idempotency_key,
        "method": "Mobile Money",
    }
    first = client.post("/payment/1", data=form)
    second = client.post("/payment/1", data=form)
    assert first.headers["Location"] == second.headers["Location"]

    receipt = client.get(first.headers["Location"])
    assert "INITIÉE (DÉMO)".encode() in receipt.data
    receipt = client.post(
        first.headers["Location"],
        data={
            "csrf_token": csrf_token(receipt),
            "action": "confirm_demo",
        },
        follow_redirects=True,
    )
    assert "SIMULÉE — NON RÉELLE".encode() in receipt.data

    with sqlite3.connect(app.config["DATABASE"]) as db:
        assert db.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1
        assert db.execute("SELECT status FROM invoices").fetchone()[0] == "À payer"


def test_csrf_token_is_required(client):
    response = client.get("/login")
    response = client.post("/login", data={"niu": DEMO_NIU})
    assert response.status_code == 400


def test_customer_cannot_read_another_receipt(client):
    login(client)
    response = client.get("/receipt/REFERENCE-INEXISTANTE")
    assert response.status_code == 404
