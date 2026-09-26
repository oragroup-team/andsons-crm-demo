# Standalone image for the @OvaEmail Slack bot - a separate Cloud Run
# service from the andSons backend, matching this being a separate folder/
# app/Slack app throughout. Plain Python only (no frontend to build), a
# fresh pip install into the image (app.py's own sys.path fallback to
# ../backend/vendor is a local-dev convenience only - that directory simply
# doesn't exist in this image's build context, so it's a no-op here).
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . ./

EXPOSE 8080
CMD ["python3", "app.py"]
