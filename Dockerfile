FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_ENV=production

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt

COPY --chown=app:app app ./app
COPY --chown=app:app config.py run.py ./

RUN mkdir -p /app/instance /catalogue /output && \
    chown -R app:app /app/instance /catalogue /output

USER app

EXPOSE 7485

CMD ["gunicorn", "--workers", "1", "--threads", "4", "--bind", "0.0.0.0:7485", "--access-logfile", "-", "--error-logfile", "-", "run:app"]
