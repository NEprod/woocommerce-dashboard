FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_ENV=production

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY requirements.txt ./
RUN apt-get update && \
    apt-get install --yes --no-install-recommends gosu && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir --requirement requirements.txt

COPY --chown=app:app app ./app
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app config.py run.py ./
COPY --chown=root:root docker/entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN mkdir -p /app/instance /catalogue /output && \
    chown -R root:root /app/app /app/migrations /app/config.py /app/run.py && \
    chmod -R a+rX /app/app /app/migrations /app/config.py /app/run.py && \
    chown -R app:app /app/instance /catalogue /output && \
    chmod 0755 /usr/local/bin/docker-entrypoint.sh

EXPOSE 7485

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["gunicorn", "--workers", "1", "--threads", "4", "--bind", "0.0.0.0:7485", "--access-logfile", "-", "--error-logfile", "-", "run:app"]
