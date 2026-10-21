"""Local administrator recovery only: no HTTP, email or bearer reset tokens."""
import getpass
from pathlib import Path
import sqlite3
import sys
import warnings

from sqlalchemy.engine import make_url
from werkzeug.security import generate_password_hash

from config import Config


def database_path():
    url = make_url(Config.SQLALCHEMY_DATABASE_URI)
    if url.drivername != 'sqlite' or not url.database or url.database == ':memory:':
        raise ValueError('Recovery requires the existing SQLite database.')
    path = Path(url.database)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[1] / 'instance' / path
    return path.resolve()


def recover_password(path, username, password):
    """Update one existing administrator hash, without starting the application."""
    if not username or len(password) < 12:
        raise ValueError('Specify an existing administrator and a password of at least 12 characters.')
    # mode=rw never creates a new database; no migrations, tasks or remote calls.
    with sqlite3.connect(Path(path).resolve().as_uri() + '?mode=rw', uri=True, timeout=5) as connection:
        row = connection.execute(
            'SELECT id, password FROM user WHERE username=? AND is_admin=1', (username,)
        ).fetchone()
        if row is None:
            raise ValueError('Existing administrator not found; no account was changed.')
        replacement = generate_password_hash(password)
        result = connection.execute(
            'UPDATE user SET password=? WHERE id=? AND password=? AND is_admin=1',
            (replacement, row[0], row[1]),
        )
        if result.rowcount != 1:
            raise ValueError('Account changed concurrently; retry local recovery.')


def main():
    if len(sys.argv) != 1 or not sys.stdin.isatty() or not sys.stderr.isatty():
        print('Use an interactive local terminal with no arguments. No password was changed.', file=sys.stderr)
        return 1
    try:
        username = input('Existing administrator username: ').strip()
        # Refuse getpass fallback rather than ever echoing a password.
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            password = getpass.getpass('New password (at least 12 characters): ')
            confirmation = getpass.getpass('Confirm new password: ')
        if password != confirmation:
            raise ValueError('Passwords do not match; no account was changed.')
        recover_password(database_path(), username, password)
    except (EOFError, KeyboardInterrupt, getpass.GetPassWarning):
        print('Secure prompt cancelled or unavailable; no password was changed.', file=sys.stderr)
        return 1
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    except sqlite3.Error:
        print('Recovery could not update the existing database. Check local permissions and retry.', file=sys.stderr)
        return 1
    print('Administrator password updated. Sign in using the new password.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
