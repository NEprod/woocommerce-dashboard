"""Small bounded technical log under the existing persistent instance mount.

Full verification/recovery evidence remains in SQLite. This log intentionally
accepts only operational identifiers, never payloads, field values or answers.
"""
import fcntl
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER = 'tlc.automatic_publishing'


class LockedRotatingHandler(RotatingFileHandler):
    def _open(self):
        descriptor = os.open(self.baseFilename, os.O_APPEND | os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        return os.fdopen(descriptor, 'a', encoding='utf-8')

    def emit(self, record):
        descriptor = os.open(self.baseFilename + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            # Another process may have rotated the path since our previous emit.
            if self.stream:
                self.stream.close(); self.stream = None
            super().emit(record)
            if self.stream:
                self.stream.close(); self.stream = None
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def configure(app, *, max_bytes=512 * 1024, backups=3):
    directory = Path(app.instance_path) / 'logs'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER)
    for handler in list(logger.handlers):
        handler.close(); logger.removeHandler(handler)
    handler = LockedRotatingHandler(directory / 'publishing.log', maxBytes=max_bytes,
        backupCount=backups, encoding='utf-8', delay=True)
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    logger.addHandler(handler); logger.setLevel(logging.INFO); logger.propagate = False
    return handler


def event(job, state):
    try:
        logging.getLogger(LOGGER).info(json.dumps({'product_id': job.product_id,
            'operation_id': job.operation_id, 'revision': job.running_revision or job.requested_revision,
            'state': state, 'trigger': job.trigger, 'attempts': job.attempts}, sort_keys=True))
    except OSError:
        logging.getLogger(__name__).warning('Persistent publishing technical log unavailable; durable database receipt retained.')
