FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements*.txt ./
RUN pip install --no-cache-dir -r requirements-longbridge.txt && useradd -u 10001 -r -m appuser
COPY app ./app
COPY config ./config
RUN mkdir -p /data && chown -R appuser:appuser /data /app
USER appuser
EXPOSE 8000
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--workers","1"]
