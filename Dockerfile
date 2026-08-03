# Multi-stage build: Render's native Python buildpack has no Node.js, and
# this app needs both (Node to build the React frontend, Python to run the
# Flask backend that serves it). Local dev still uses the no-venv
# backend/vendor/ approach (see _vendor_path.py) - this Dockerfile is only
# for cloud deployment, where a container is already an isolated
# environment, so a plain `pip install` into it is the idiomatic choice.

FROM node:20-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=frontend-build /app/frontend/dist ../frontend/dist

EXPOSE 5001
CMD ["python3", "app.py"]
