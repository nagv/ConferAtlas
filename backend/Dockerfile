FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend /app/backend
COPY core /app/core
COPY shared /app/shared
COPY skills /app/skills
COPY data /app/data
COPY scripts /app/scripts
EXPOSE 8000
# One worker: datasets are held in this process's memory.
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
