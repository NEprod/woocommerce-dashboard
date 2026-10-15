"""Admin-only LOCAL template authoring. No catalogue or remote writes here."""
import json

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from app import db
from app.models import CustomisationTemplate
from app import customisation_templates as service

customisations = Blueprint('customisations', __name__, url_prefix='/customisations')


@customisations.before_request
@login_required
def admin():
    if not current_user.is_admin: abort(403)


@customisations.get('/templates')
def index():
    return render_template('customisation_templates.html')


@customisations.route('/api/templates', methods=['GET', 'POST'])
def templates():
    if request.method == 'GET': return jsonify(items=service.library())
    return save()


@customisations.route('/api/templates/<template_id>', methods=['GET', 'POST'])
def template(template_id):
    if request.method == 'POST': return save(template_id)
    revision = request.args.get('revision', type=int)
    query = CustomisationTemplate.query.filter_by(template_id=template_id)
    item = query.filter_by(revision=revision).first_or_404() if revision else query.order_by(CustomisationTemplate.revision.desc()).first_or_404()
    return jsonify(service.view(item))


def save(template_id=None):
    from app.metadata_edit import _object
    try: payload = json.loads(request.get_data(), object_pairs_hook=_object)
    except (ValueError, UnicodeError): return jsonify(error='Use valid JSON with unique object and field keys.'), 400
    if not isinstance(payload, dict): return jsonify(error='Submit a template object.'), 400
    try: return jsonify(service.save(payload, template_id))
    except service.TemplateConflict as error: return jsonify(error=str(error)), 409
    except (ValueError, TypeError) as error: return jsonify(error=str(error)), 400
