# Builds the API image, and the dashboard it serves.
#
# The dashboard is built here rather than shipped as a separate container so
# that one host answers for both: one URL to open or share, and no CORS to
# configure. VITE_API_BASE is empty on purpose -- that tells the dashboard to
# call the same origin it was served from. Vite inlines env vars at build time,
# so this has to be decided here and not at run time.
#
# The build context is the REPOSITORY ROOT, not api/ (see infra/docker-compose.yml),
# because this stage needs ../admin.

FROM node:20-slim AS dashboard
WORKDIR /build
COPY admin/package.json admin/package-lock.json ./
RUN npm ci
COPY admin/ ./
ENV VITE_API_BASE=""
RUN npm run build


FROM python:3.12-slim

WORKDIR /srv

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq5 curl \
 && rm -rf /var/lib/apt/lists/*

COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/ .
COPY --from=dashboard /build/dist /srv/dashboard

ENV PYTHONUNBUFFERED=1 \
    STARNET_DASHBOARD_DIR=/srv/dashboard
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
