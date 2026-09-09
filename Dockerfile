FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .
# Crawl mode renders a public page, so the image ships the browser it drives.
RUN playwright install --with-deps chromium
COPY app app
COPY scripts scripts
COPY alembic alembic
COPY alembic.ini .
EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
