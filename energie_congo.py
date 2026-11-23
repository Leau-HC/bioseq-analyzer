import os
import secrets
import sqlite3
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)


ROOT = Path(__file__).resolve().parent
DEMO_NIU = "NIU-DEMO-001"
DEMO_OTP = "246810"
PAYMENT_METHODS = ("Mobile Money", "Airtel Money", "Carte bancaire")

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY,
    niu TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    address TEXT NOT NULL,
    district TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meters (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    reference TEXT NOT NULL UNIQUE,
    address TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS meter_readings (
    id INTEGER PRIMARY KEY,
    meter_id INTEGER NOT NULL REFERENCES meters(id),
    billing_period TEXT NOT NULL,
    consumption_kwh INTEGER NOT NULL CHECK (consumption_kwh >= 0),
    read_at TEXT NOT NULL,
    UNIQUE (meter_id, billing_period)
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY,
    meter_id INTEGER NOT NULL REFERENCES meters(id),
    invoice_number TEXT NOT NULL UNIQUE,
    billing_period TEXT NOT NULL,
    consumption_kwh INTEGER NOT NULL CHECK (consumption_kwh >= 0),
    amount_cfa INTEGER NOT NULL CHECK (amount_cfa >= 0),
    due_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'À payer'
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    amount_cfa INTEGER NOT NULL CHECK (amount_cfa >= 0),
    method TEXT NOT NULL,
    status TEXT NOT NULL,
    reference TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    confirmed_at TEXT
);
"""


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        connection = sqlite3.connect(current_app.config["DATABASE"])
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        g.db = connection
    return g.db


def close_db(_error: BaseException | None = None) -> None:
    del _error
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def initialize_database(app: Flask) -> None:
    database = Path(app.config["DATABASE"])
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as db:
        db.executescript(SCHEMA)
        if db.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0:
            db.execute(
                """
                INSERT INTO customers (niu, full_name, phone, address, district)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    DEMO_NIU,
                    "Jean-Marc Exemple",
                    "+242 06 ** ** 42",
                    "Avenue de la Paix, Brazzaville",
                    "Moungali",
                ),
            )
            customer_id = db.execute(
                "SELECT id FROM customers WHERE niu = ?", (DEMO_NIU,)
            ).fetchone()[0]
            db.execute(
                """
                INSERT INTO meters (customer_id, reference, address)
                VALUES (?, ?, ?)
                """,
                (
                    customer_id,
                    "E2C-CPT-000123",
                    "Avenue de la Paix, Brazzaville",
                ),
            )
            meter_id = db.execute(
                "SELECT id FROM meters WHERE reference = ?",
                ("E2C-CPT-000123",),
            ).fetchone()[0]
            db.executemany(
                """
                INSERT INTO meter_readings
                    (meter_id, billing_period, consumption_kwh, read_at)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (meter_id, "Septembre 2026", 172, "2026-10-01"),
                    (meter_id, "Octobre 2026", 184, "2026-11-01"),
                ],
            )
            db.execute(
                """
                INSERT INTO invoices (
                    meter_id, invoice_number, billing_period,
                    consumption_kwh, amount_cfa, due_date, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meter_id,
                    "FAC-DEMO-2026-001",
                    "Octobre 2026",
                    184,
                    45000,
                    "30 novembre 2026",
                    "À payer",
                ),
            )


def csrf_protected() -> bool:
    submitted = request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    if not submitted or not expected or not secrets.compare_digest(submitted, expected):
        abort(400, description="Jeton de formulaire invalide. Rechargez la page.")
    return True


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "customer_id" not in session:
            return redirect(url_for("login"))
        customer = get_db().execute(
            "SELECT id FROM customers WHERE id = ?", (session["customer_id"],)
        ).fetchone()
        if customer is None:
            session.clear()
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def owned_invoice(invoice_id: int, customer_id: int):
    return get_db().execute(
        """
        SELECT invoices.*, meters.reference AS meter_reference
        FROM invoices
        JOIN meters ON meters.id = invoices.meter_id
        WHERE invoices.id = ? AND meters.customer_id = ?
        """,
        (invoice_id, customer_id),
    ).fetchone()


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
        DATABASE=os.environ.get("ENERGIE_DATABASE")
        or str(ROOT / "energie_congo_demo.db"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
        PERMANENT_SESSION_LIFETIME=1800,
    )
    if test_config:
        app.config.update(test_config)

    initialize_database(app)
    app.teardown_appcontext(close_db)

    @app.before_request
    def ensure_csrf_token() -> None:
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)

    @app.context_processor
    def template_context() -> dict:
        return {"csrf_token": session.get("csrf_token", "")}

    @app.route("/")
    def index():
        if "customer_id" in session:
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            csrf_protected()
            niu = request.form.get("niu", "").strip()
            customer = get_db().execute(
                "SELECT id FROM customers WHERE niu = ?", (niu,)
            ).fetchone()
            if customer is None:
                flash("Identifiant de démonstration inconnu.", "error")
            else:
                session.clear()
                session["pending_customer_id"] = customer["id"]
                session["csrf_token"] = secrets.token_urlsafe(32)
                return redirect(url_for("verify_otp"))

        return render_template("login.html")

    @app.route("/verify", methods=["GET", "POST"])
    def verify_otp():
        if "pending_customer_id" not in session:
            return redirect(url_for("login"))

        if request.method == "POST":
            csrf_protected()
            if secrets.compare_digest(request.form.get("otp", ""), DEMO_OTP):
                customer_id = session.pop("pending_customer_id")
                session.clear()
                session["customer_id"] = customer_id
                session["csrf_token"] = secrets.token_urlsafe(32)
                session.permanent = True
                return redirect(url_for("dashboard"))
            flash("Code de démonstration incorrect.", "error")

        return render_template("verify.html")

    @app.route("/dashboard")
    @login_required
    def dashboard():
        customer_id = session["customer_id"]
        db = get_db()
        customer = db.execute(
            "SELECT * FROM customers WHERE id = ?", (customer_id,)
        ).fetchone()
        meters = db.execute(
            """
            SELECT meters.*, invoices.id AS invoice_id,
                   invoices.invoice_number, invoices.billing_period,
                   invoices.consumption_kwh, invoices.amount_cfa,
                   invoices.due_date, invoices.status AS invoice_status
            FROM meters
            LEFT JOIN invoices ON invoices.meter_id = meters.id
            WHERE meters.customer_id = ? AND meters.active = 1
            ORDER BY invoices.id DESC
            """,
            (customer_id,),
        ).fetchall()
        readings = db.execute(
            """
            SELECT meter_readings.*
            FROM meter_readings
            JOIN meters ON meters.id = meter_readings.meter_id
            WHERE meters.customer_id = ?
            ORDER BY meter_readings.id DESC
            LIMIT 6
            """,
            (customer_id,),
        ).fetchall()
        payments = db.execute(
            """
            SELECT payments.*
            FROM payments
            WHERE customer_id = ?
            ORDER BY id DESC
            LIMIT 10
            """,
            (customer_id,),
        ).fetchall()
        return render_template(
            "dashboard.html",
            customer=customer,
            meters=meters,
            readings=readings,
            payments=payments,
        )

    @app.route("/payment/<int:invoice_id>", methods=["GET", "POST"])
    @login_required
    def payment(invoice_id: int):
        customer_id = session["customer_id"]
        invoice = owned_invoice(invoice_id, customer_id)
        if invoice is None:
            abort(404)
        if invoice["status"] != "À payer":
            flash("Cette facture n'est pas disponible pour une simulation.", "error")
            return redirect(url_for("dashboard"))

        if request.method == "POST":
            csrf_protected()
            method = request.form.get("method", "")
            idempotency_key = request.form.get("idempotency_key", "")
            if method not in PAYMENT_METHODS:
                flash("Sélectionnez un moyen de paiement valide.", "error")
                return redirect(url_for("payment", invoice_id=invoice_id))
            if not idempotency_key or len(idempotency_key) > 100:
                abort(400, description="Clé de formulaire invalide.")

            db = get_db()
            existing = db.execute(
                "SELECT reference FROM payments WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                return redirect(url_for("receipt", reference=existing["reference"]))

            reference = "DEMO-" + secrets.token_hex(5).upper()
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            try:
                with db:
                    db.execute(
                        """
                        INSERT INTO payments (
                            customer_id, invoice_id, amount_cfa, method,
                            status, reference, idempotency_key, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            customer_id,
                            invoice_id,
                            invoice["amount_cfa"],
                            method,
                            "INITIÉE (DÉMO)",
                            reference,
                            idempotency_key,
                            now,
                        ),
                    )
            except sqlite3.IntegrityError:
                existing = db.execute(
                    "SELECT reference FROM payments WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing is None:
                    raise
                reference = existing["reference"]
            return redirect(url_for("receipt", reference=reference))

        return render_template(
            "payment.html",
            invoice=invoice,
            methods=PAYMENT_METHODS,
            idempotency_key=secrets.token_urlsafe(24),
        )

    @app.route("/receipt/<reference>", methods=["GET", "POST"])
    @login_required
    def receipt(reference: str):
        db = get_db()
        payment_record = db.execute(
            """
            SELECT payments.*, invoices.invoice_number,
                   invoices.billing_period
            FROM payments
            JOIN invoices ON invoices.id = payments.invoice_id
            WHERE payments.reference = ? AND payments.customer_id = ?
            """,
            (reference, session["customer_id"]),
        ).fetchone()
        if payment_record is None:
            abort(404)

        if request.method == "POST":
            csrf_protected()
            if (
                request.form.get("action") == "confirm_demo"
                and payment_record["status"] == "INITIÉE (DÉMO)"
            ):
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                with db:
                    db.execute(
                        """
                        UPDATE payments
                        SET status = ?, confirmed_at = ?
                        WHERE id = ? AND customer_id = ?
                          AND status = 'INITIÉE (DÉMO)'
                        """,
                        (
                            "SIMULÉE — NON RÉELLE",
                            now,
                            payment_record["id"],
                            session["customer_id"],
                        ),
                    )
            return redirect(url_for("receipt", reference=reference))

        return render_template("receipt.html", payment=payment_record)

    @app.route("/logout", methods=["POST"])
    @login_required
    def logout():
        csrf_protected()
        session.clear()
        return redirect(url_for("login"))

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
