"""Administrator-only configuration. GET never contacts either store."""
from flask import Blueprint, abort, render_template, request, redirect, url_for
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SelectField, HiddenField

from app import commerce_config as config
from app.shopify_connection import ShopifyAdapter, ShopifyConfiguration, ShopifyError, VERSIONS

commerce = Blueprint("commerce", __name__)


class ConnectionForm(FlaskForm):
    domain = StringField("Shopify store domain")
    token = PasswordField("Admin API access token")
    api_version = SelectField("GraphQL Admin API version", choices=[(v, v) for v in VERSIONS], default="2026-10")
    revision = HiddenField()


@commerce.route("/settings/commerce", methods=["GET", "POST"])
@login_required
def settings():
    if not current_user.is_admin:
        abort(403)
    form = ConnectionForm()
    result, error, saved = None, None, False
    try:
        active = config.active_platform()
        stored = config.shopify_settings()
        owned = config.deployment_owned()
        if request.method == "GET":
            form.domain.data = stored.get("domain", "")
            form.api_version.data = stored.get("api_version", "2026-10")
            form.revision.data = config.revision()
        elif form.validate_on_submit():
            if request.form.get("active_platform", active) != active:
                raise config.CommerceConfigError("Platform changes require a separate reviewed migration; activation is not available here.")
            if form.revision.data != config.revision():
                raise config.CommerceConfigError("Settings changed. Reload before testing or saving.")
            action = request.form.get("action")
            if action not in {"test", "save"}:
                abort(400)
            if owned:
                if action == "save":
                    raise config.CommerceConfigError("Shopify settings are deployment-owned. Update your environment instead.")
                candidate = ShopifyConfiguration(stored.get("domain", ""), stored.get("token", ""), stored.get("api_version", "2026-10"))
            else:
                token = form.token.data or ""
                domain = (form.domain.data or "").strip().lower()
                if not token and domain == stored.get("domain"):
                    token = stored.get("token", "")
                candidate = ShopifyConfiguration(domain, token, form.api_version.data)
            candidate = candidate.validated()
            result = ShopifyAdapter().test_connection(candidate)
            if action == "save":
                config.save_shopify(dict(domain=candidate.domain, token=candidate.token,
                                         api_version=candidate.api_version, shop_id=result["shop_id"]), form.revision.data)
                saved = True
                form.revision.data = config.revision()
            form.domain.data, form.api_version.data = candidate.domain, candidate.api_version
        elif request.method == "POST":
            error = "Invalid form or API version. Reload and try again."
    except (config.CommerceConfigError, ShopifyError) as exc:
        error = str(exc)
        active, stored, owned = "unavailable", {}, False
        # Never fall back to Woo if protected configuration is corrupt.
        try:
            active, stored, owned = config.active_platform(), config.shopify_settings(), config.deployment_owned()
        except config.CommerceConfigError:
            pass
    form.token.data = None  # No password redisplay, including rejected submissions.
    response = render_template("commerce_settings.html", form=form, active=active,
        owned=owned, has_token=bool(stored.get("token")), result=result, error=error, saved=saved)
    return response, 400 if error else 200, {"Cache-Control": "no-store"}


def guard_platform():
    """Do not execute Woo workflows on a Shopify installation. Woo path is untouched."""
    endpoint = request.endpoint or ""
    if endpoint.startswith(("main.woocommerce", "taxonomy.sync")) or endpoint in {"main.woo_sync", "main.woo_live_refresh", "main.sync"}:
        try:
            selected = config.active_platform()
        except config.CommerceConfigError:
            abort(503, "Commerce configuration requires administrator review.")
        if selected == "shopify":
            if request.method == "GET":
                return redirect(url_for("commerce.settings"))
            abort(409, "Woo workflows are disabled for this Shopify installation; Shopify publishing is not implemented.")
