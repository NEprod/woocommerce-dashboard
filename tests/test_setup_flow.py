import pytest

from app import create_app, db
from app.models import User
from config import Config


@pytest.fixture
def setup_app(tmp_path):
    database = tmp_path / "instance" / "site.db"
    database.parent.mkdir()
    original_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{database}"
    try:
        app = create_app()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        yield app, database
    finally:
        with app.app_context():
            db.session.remove()
        Config.SQLALCHEMY_DATABASE_URI = original_uri


def test_setup_get_and_valid_post_create_admin_and_continue(setup_app):
    app, database = setup_app
    client = app.test_client()

    response = client.get("/setup")
    assert response.status_code == 200

    response = client.post(
        "/setup",
        data={
            "email": "admin@example.com",
            "username": "fixture-admin",
            "password": "fixture-password",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/initial-settings")
    with app.app_context():
        user = User.query.one()
        assert user.email == "admin@example.com"
        assert user.username == "fixture-admin"
    assert client.get("/initial-settings").status_code == 200
    assert database.is_file()


def test_setup_invalid_email_returns_form_validation_response(setup_app):
    app, _database = setup_app
    client = app.test_client()

    response = client.post(
        "/setup",
        data={
            "email": "not-an-email",
            "username": "fixture-admin",
            "password": "fixture-password",
        },
    )

    assert response.status_code == 200
    assert b'email' in response.data.lower()
    with app.app_context():
        assert User.query.count() == 0


def test_setup_referenced_local_assets_exist(setup_app):
    app, _database = setup_app
    client = app.test_client()

    response = client.get("/setup")
    assert response.status_code == 200
    assert b"/static/assets/img/woocommerce-dashboard-icon.png" in response.data
    assert b"/static/assets/img/favicon/favicon-32x32.png" in response.data
    assert (
        client.get("/static/assets/img/woocommerce-dashboard-logo.svg").status_code
        == 200
    )
    assert (
        client.get("/static/assets/img/favicon/favicon-32x32.png").status_code
        == 200
    )
