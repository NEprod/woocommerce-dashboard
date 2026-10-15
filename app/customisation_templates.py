"""Small versioned library in the existing persistent SQLite database."""
import json
import uuid
from copy import deepcopy

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from flask import g

from app import db
from app.models import CustomisationTemplate
from app.customisations import validate


class TemplateConflict(ValueError):
    pass


def definition(reference):
    key = (reference['id'], reference['revision'])
    cache = g.setdefault('customisation_template_definitions', {})
    if key in cache: return deepcopy(cache[key])
    item = db.session.get(CustomisationTemplate, (reference['id'], reference['revision']))
    if item is None:
        raise ValueError('Pinned customisation template revision is unavailable; restore it before saving/scanning.')
    cache[key] = json.loads(item.definition_json)
    return deepcopy(cache[key])


def view(item, *, content=True):
    result = {'id': item.template_id, 'revision': item.revision, 'name': item.name,
        'description': item.description, 'archived': item.archived, 'created_at': item.created_at.isoformat()}
    if content: result['definition'] = json.loads(item.definition_json)
    return result


def library():
    heads = db.session.query(CustomisationTemplate.template_id,
        func.max(CustomisationTemplate.revision).label('revision')).group_by(CustomisationTemplate.template_id).subquery()
    return [view(row, content=False) for row in CustomisationTemplate.query.join(heads,
        (CustomisationTemplate.template_id == heads.c.template_id) &
        (CustomisationTemplate.revision == heads.c.revision)).order_by(CustomisationTemplate.name)]


def save(payload, template_id=None):
    latest = None
    if template_id:
        latest = CustomisationTemplate.query.filter_by(template_id=template_id).order_by(CustomisationTemplate.revision.desc()).first()
        if not latest or type(payload.get('expected_revision')) is not int or payload['expected_revision'] != latest.revision:
            raise TemplateConflict('Template changed; reload before saving. Existing product assignments are unchanged.')
    name, description = payload.get('name'), payload.get('description', '')
    if not isinstance(name, str) or not name.strip() or len(name) > 191 or not isinstance(description, str) or len(description) > 4000:
        raise ValueError('Provide a template name (1–191 characters) and bounded internal description.')
    content = deepcopy(payload.get('definition'))
    if not isinstance(content, dict) or 'template' in content:
        raise ValueError('Template definitions must be standalone; nested template references are not supported.')
    validate(content)
    archived = payload.get('archived', False)
    if type(archived) is not bool: raise ValueError('Archived must be boolean.')
    text = json.dumps(content, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if latest and (latest.name, latest.description, latest.definition_json, latest.archived) == (name.strip(), description, text, archived):
        return view(latest)
    item = CustomisationTemplate(template_id=template_id or 't_' + uuid.uuid4().hex,
        revision=latest.revision + 1 if latest else 1, name=name.strip(), description=description,
        definition_json=text, archived=archived)
    db.session.add(item)
    try: db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise TemplateConflict('Another editor saved this template revision first. Reload before retrying.') from None
    return view(item)


def check_assignments(before, after):
    """Archive blocks NEW references, never breaks a previously pinned source."""
    reference = after.get('customisation', {}).get('template')
    previous = before.get('customisation', {}).get('template')
    if not reference or reference == previous: return
    definition(reference)
    latest = CustomisationTemplate.query.filter_by(template_id=reference['id']).order_by(CustomisationTemplate.revision.desc()).first()
    if latest.archived:
        raise ValueError('This customisation template is archived. Existing pinned assignments remain valid; choose an active template for new assignments.')
