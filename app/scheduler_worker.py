"""Dedicated in-container worker; never starts from a Flask/browser request."""
import signal
import threading
import uuid
from app import create_app, db
from app.scheduled_tasks import tick
from app.utils.operation_control import sanitize_operation_error


def main():
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_:stop.set())
    app=create_app(recover=False)
    owner=uuid.uuid4().hex
    while not stop.is_set():
        with app.app_context():
            try:
                tick(owner)
            except Exception as exc:
                db.session.rollback()
                app.logger.warning("Scheduled worker needs attention: %s",sanitize_operation_error(exc))
            finally:
                db.session.remove()
        stop.wait(2)


if __name__ == "__main__":
    main()
