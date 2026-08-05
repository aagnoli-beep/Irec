FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY irec ./irec
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --no-cache-dir .

# Utente non privilegiato: una compromissione del processo non deve
# ottenere root nel container.
RUN useradd --system --no-create-home irec
USER irec

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

CMD ["uvicorn", "irec.main:app", "--host", "0.0.0.0", "--port", "8000"]
