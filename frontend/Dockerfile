FROM python:3.12-slim
WORKDIR /app
COPY frontend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY frontend /app/frontend
COPY shared /app/shared
WORKDIR /app/frontend
EXPOSE 8501
# Bound to all interfaces inside the container, so staff sign-in is always required (see README).
CMD ["python", "-m", "streamlit", "run", "app.py", "--server.address", "0.0.0.0", "--server.port", "8501", "--server.headless", "true", "--browser.gatherUsageStats", "false"]
