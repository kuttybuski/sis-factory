FROM docker.io/library/python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir fastapi uvicorn
COPY mock_externals.py /app/mock_externals.py
WORKDIR /app
CMD ["uvicorn", "mock_externals:app", "--host", "0.0.0.0", "--port", "8080"]
