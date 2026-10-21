"""Disposable accounts only; recovery never calls an external service."""
import getpass
import re
import sqlite3
import warnings

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from app import db
from app import admin_recovery
from app.models import User
from app.utils.token_utils import generate_reset_token, verify_reset_token
from test_phase3_woo_publish_preview import preview_app


OLD_PASSWORD = 'fictional-original-password'
NEW_PASSWORD = 'fictional-recovered-password'


@pytest.fixture
def account(preview_app):
    with preview_app.app_context():
        user = db.session.get(User, 1)
        user.password = generate_password_hash(OLD_PASSWORD)
        db.session.commit()
        original = user.password
        path = db.engine.url.database
    return preview_app, path, original


@pytest.mark.parametrize('method', ['get', 'post'])
@pytest.mark.parametrize('email', ['preview@example.test', 'unknown@example.test'])
def test_recovery_is_inert_and_does_not_disclose_accounts(account, monkeypatch, capsys, caplog, method, email):
    app, path, original = account
    def forbidden(*args, **kwargs):
        pytest.fail('Disabled recovery must not generate a token')
    monkeypatch.setattr('app.utils.token_utils.generate_reset_token', forbidden)
    response = getattr(app.test_client(), method)('/forgot-password', data={'email': email})
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'Self-service password recovery is currently unavailable' in body
    assert 'No reset email will be sent' in body and '<form' not in body
    assert 'a reset link has been sent' not in body and email not in body
    with sqlite3.connect(path) as connection:
        assert connection.execute('select password from user where id=1').fetchone()[0] == original
    captured = capsys.readouterr()
    assert captured.out == captured.err == ''
    assert email not in caplog.text and 'reset-password/' not in caplog.text


@pytest.mark.parametrize('method', ['get', 'post'])
def test_previously_usable_token_cannot_reset_or_leak(account, capsys, caplog, method):
    app, path, original = account
    with app.app_context():
        token = generate_reset_token('preview@example.test')
        assert verify_reset_token(token) == 'preview@example.test'
    response = getattr(app.test_client(), method)('/reset-password/' + token,
        data={'password': NEW_PASSWORD, 'confirm': NEW_PASSWORD})
    assert response.status_code == 302 and response.location == '/forgot-password'
    captured = capsys.readouterr()
    assert token not in response.get_data(as_text=True) + captured.out + captured.err + caplog.text
    assert NEW_PASSWORD not in captured.out + captured.err + caplog.text
    with sqlite3.connect(path) as connection:
        assert connection.execute('select password from user where id=1').fetchone()[0] == original


def test_login_csrf_sessions_and_logout_unchanged(account):
    app, _, _ = account
    app.config['WTF_CSRF_ENABLED'] = True
    client = app.test_client()
    assert client.post('/login', data={'username': 'preview-admin', 'password': OLD_PASSWORD}).status_code == 400
    page = client.get('/login').get_data(as_text=True)
    csrf = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page).group(1)
    assert client.post('/forgot-password', data={'email': 'preview@example.test'}).status_code == 400
    response = client.post('/forgot-password', data={'csrf_token': csrf, 'email': 'preview@example.test'})
    assert 'currently unavailable' in response.get_data(as_text=True)
    bad = client.post('/login', data={'csrf_token': csrf, 'username': 'preview-admin', 'password': 'wrong'})
    assert 'Invalid username or password' in bad.get_data(as_text=True)
    response = client.post('/login', data={'csrf_token': csrf, 'username': 'preview-admin', 'password': OLD_PASSWORD, 'remember': 'y'})
    assert response.status_code == 302 and 'remember_token=' in response.headers.get('Set-Cookie', '')
    with client.session_transaction() as session:
        assert session['_user_id'] == '1' and session['_fresh'] is True
    client.get('/forgot-password')
    with client.session_transaction() as session:
        assert session['_user_id'] == '1'
    client.get('/logout')
    with client.session_transaction() as session:
        assert '_user_id' not in session


def test_local_recovery_changes_only_existing_admin_and_persists(account, capsys, caplog):
    app, path, _ = account
    with sqlite3.connect(path) as connection:
        before = connection.execute('select id,email,username,is_admin from user').fetchall()
        tables = connection.execute("select name from sqlite_master where type='table'").fetchall()
        counts = {name: connection.execute('select count(*) from "' + name + '"').fetchone()[0] for name, in tables}
    admin_recovery.recover_password(path, 'preview-admin', NEW_PASSWORD)
    with sqlite3.connect(path) as connection:
        assert connection.execute('select id,email,username,is_admin from user').fetchall() == before
        assert all(connection.execute('select count(*) from "' + name + '"').fetchone()[0] == count for name, count in counts.items())
        retained = connection.execute('select password from user where id=1').fetchone()[0]
        assert check_password_hash(retained, NEW_PASSWORD) and not check_password_hash(retained, OLD_PASSWORD)
    client = app.test_client()
    assert client.post('/login', data={'username': 'preview-admin', 'password': OLD_PASSWORD}).status_code == 200
    assert client.post('/login', data={'username': 'preview-admin', 'password': NEW_PASSWORD}).status_code == 302
    captured = capsys.readouterr()
    assert NEW_PASSWORD not in captured.out + captured.err + caplog.text and retained not in caplog.text


@pytest.mark.parametrize('username,password', [('unknown', NEW_PASSWORD), ('preview-admin', 'short')])
def test_invalid_local_recovery_never_changes_account(account, username, password):
    _, path, original = account
    with pytest.raises(ValueError):
        admin_recovery.recover_password(path, username, password)
    with sqlite3.connect(path) as connection:
        assert connection.execute('select password from user where id=1').fetchone()[0] == original


def test_local_recovery_cannot_change_nonadmin_or_create_database(account, tmp_path):
    _, path, original = account
    with sqlite3.connect(path) as connection:
        connection.execute('update user set is_admin=0 where id=1')
    with pytest.raises(ValueError, match='administrator not found'):
        admin_recovery.recover_password(path, 'preview-admin', NEW_PASSWORD)
    missing = tmp_path / 'must-not-be-created.db'
    with pytest.raises(sqlite3.Error):
        admin_recovery.recover_password(missing, 'preview-admin', NEW_PASSWORD)
    assert not missing.exists()
    with sqlite3.connect(path) as connection:
        assert connection.execute('select password from user where id=1').fetchone()[0] == original


def test_cli_requires_interactive_terminal_and_no_password_arguments(monkeypatch, capsys):
    monkeypatch.setattr(admin_recovery.sys, 'argv', ['admin_recovery'])
    monkeypatch.setattr(admin_recovery.sys.stdin, 'isatty', lambda: False)
    assert admin_recovery.main() == 1
    monkeypatch.setattr(admin_recovery.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(admin_recovery.sys.stderr, 'isatty', lambda: True)
    monkeypatch.setattr(admin_recovery.sys, 'argv', ['admin_recovery', '--password', NEW_PASSWORD])
    assert admin_recovery.main() == 1
    assert NEW_PASSWORD not in capsys.readouterr().err


@pytest.mark.parametrize('mode', ['success', 'mismatch', 'unsafe_prompt'])
def test_cli_hidden_prompts_never_echo_password(account, monkeypatch, capsys, mode):
    _, path, original = account
    monkeypatch.setattr(admin_recovery.sys, 'argv', ['admin_recovery'])
    monkeypatch.setattr(admin_recovery.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(admin_recovery.sys.stderr, 'isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda prompt: 'preview-admin')
    monkeypatch.setattr(admin_recovery, 'database_path', lambda: path)
    answers = iter([NEW_PASSWORD, NEW_PASSWORD if mode == 'success' else 'different'])
    def hidden(prompt):
        if mode == 'unsafe_prompt':
            warnings.warn('Cannot control echo', getpass.GetPassWarning)
        return next(answers)
    monkeypatch.setattr(admin_recovery.getpass, 'getpass', hidden)
    assert admin_recovery.main() == (0 if mode == 'success' else 1)
    captured = capsys.readouterr()
    assert NEW_PASSWORD not in captured.out + captured.err and original not in captured.out + captured.err
    with sqlite3.connect(path) as connection:
        retained = connection.execute('select password from user where id=1').fetchone()[0]
    assert check_password_hash(retained, NEW_PASSWORD) if mode == 'success' else retained == original


def test_local_command_resolves_existing_configured_database(account):
    _, path, _ = account
    assert str(admin_recovery.database_path()) == path
