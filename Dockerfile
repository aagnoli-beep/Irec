FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY irec ./irec
RUN pip install --no-cache-dir .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

CMD ["uvicorn", "irec.main:app", "--host", "0.0.0.0", "--port", "8000"]
