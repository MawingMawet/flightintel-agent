# App-only image (PHASE3_PLAN Q4): the products database is mounted
# read-only at run time, secrets arrive as env vars, and the vector
# store stays in the host's WSL Postgres (host networking in compose).
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["uvicorn", "flightintel.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
