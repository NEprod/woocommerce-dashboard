import os
from flask import Flask, request
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from config import Config
from werkzeug.security import generate_password_hash
from datetime import datetime

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()


def create_app():
    from .security import validate_secret_key

    secret_key = validate_secret_key()
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    app.config["SECRET_KEY"] = secret_key

    app.config["DISCORD_WEBHOOK_URL"] = os.getenv("DISCORD_WEBHOOK_URL", "")
    app.config["DISCORD_ENABLED"] = (
        os.getenv("DISCORD_ENABLED", "false").lower() == "true"
    )
    app.config["DISCORD_USERNAME"] = os.getenv("DISCORD_USERNAME", "Woo Scanner")
    app.config["DISCORD_AVATAR_URL"] = os.getenv("DISCORD_AVATAR_URL", "")

    db.init_app(app)
    login_manager.init_app(app)

    @app.before_request
    def taxonomy_request_bounds():
        # Apply before CSRF parses multipart/form data; other workflows unchanged.
        if request.blueprint == "taxonomy":
            request.max_content_length = 4 * 1024 * 1024
            request.max_form_memory_size = 2 * 1024 * 1024
            request.max_form_parts = 50

    csrf.init_app(app)

    from .models import User, Product, Variation, Service

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from .routes import main

    app.register_blueprint(main)
    from .taxonomy_routes import taxonomy
    app.register_blueprint(taxonomy)

    @app.context_processor
    def inject_current_year():
        return {"current_year": datetime.now().year}

    with app.app_context():
        from .database import ensure_database
        from .product_relationships import recover_relationship_transactions
        from .utils.operation_control import recover_interrupted_operations

        app.config["DATABASE_MIGRATION_REPORT"] = ensure_database(str(db.engine.url))
        # Existing consumers keep using Settings; deployment values are resolved
        # by its descriptors, never copied into competing persisted settings.
        from .models import Settings
        if any(app.config.get(key) is not None for key in ("PRODUCT_FOLDER", "OUTPUT_FOLDER", "URL_PREFIX")) and Settings.query.first() is None:
            db.session.add(Settings())
            db.session.commit()
        app.config["RELATIONSHIP_TRANSACTIONS_RECOVERED"] = (
            recover_relationship_transactions()
        )
        app.config["INTERRUPTED_OPERATIONS_RECOVERED"] = (
            recover_interrupted_operations()
        )

    return app
