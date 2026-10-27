"""Supervise the existing web server and scheduler as one Docker lifetime."""
import os
import signal
import subprocess
import sys
import time


def main():
    from app import create_app, db
    os.environ["TLC_MANAGED_RUNTIME"]="true"
    os.environ['TLC_SCHEDULER_READY_AT']=str(time.time()+600)
    app=create_app(recover=True)  # migration/startup recovery ONCE before children
    with app.app_context():
        from app.catalogue_startup import retire
        retire()  # no forced full Shopify sweep or local scan on a configured restart
        from app.scheduled_tasks import enqueue, ORDERS_KEY
        if app.config.get('SHOPIFY_ORDERS_STARTUP_ENABLED',True):
            enqueue(ORDERS_KEY,startup=True)
        db.session.remove();db.engine.dispose()
    children=[]
    stopping=False
    def stop(*_):
        nonlocal stopping
        stopping=True
        for child in children:
            if child.poll() is None:child.terminate()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,stop)
    try:
        children.append(subprocess.Popen(sys.argv[1:] or ["gunicorn","--workers","1","--threads","4","--bind","0.0.0.0:7485","run:app"]))
        children.append(subprocess.Popen([sys.executable,"-m","app.scheduler_worker"]))
        while not stopping and all(child.poll() is None for child in children):time.sleep(.5)
        failed=not stopping
    finally:
        stop()
        for child in children:
            try:child.wait(timeout=25)
            except subprocess.TimeoutExpired:child.kill();child.wait()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
