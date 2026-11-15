FROM node:22-alpine AS dashboard
WORKDIR /build
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY backend ./backend
COPY cli ./cli
COPY --from=dashboard /build/dist ./backend/static
RUN pip install --no-cache-dir . && useradd --create-home --uid 10001 tokenscope && mkdir /data && chown tokenscope /data
USER tokenscope
ENV TOKENSCOPE_DATA_DIR=/data
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.api.server:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
