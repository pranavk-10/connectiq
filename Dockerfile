FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 connectiq
COPY --chown=connectiq:connectiq . .
RUN mkdir -p /app/data && chown connectiq:connectiq /app/data
USER connectiq
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 CMD python -m connectiq.cli health --http || exit 1
CMD ["python", "-m", "connectiq.cli", "serve"]
