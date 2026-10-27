"""Dedicated in-container worker; never starts from a Flask/browser request."""
import signal
import threading
import uuid
import os
import time
from app import create_app, db
from app.scheduled_tasks import tick
from app.utils.operation_control import sanitize_operation_error


def main():
    os.environ.setdefault('TLC_SCHEDULER_READY_AT',str(time.time()+600))
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_:stop.set())
    app=create_app(recover=False)
    from app.publishing_logs import configure
    try:
        configure(app)
    except OSError:
        app.logger.warning('Publishing technical log unavailable; database receipts remain authoritative.')
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
