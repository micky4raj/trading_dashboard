# Production image for the Dhan Algo Trading Dashboard.
FROM python:3.11-slim

WORKDIR /app

# System deps kept minimal; add build-essential here if a pinned dep needs compiling.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/data

# Credentials are injected at runtime via environment variables / a mounted
# .env file — never baked into the image. See README "Security notes".
ENV TRADING_DB_PATH=/app/data/trading_journal.db \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

CMD ["streamlit", "run", "app.py"]
