# Two targets. `dev` is what docker-compose uses locally: the Vite dev server,
# with hot reload. The default (final) target is a production image: the app is
# built to static assets and served by nginx.
#
# A Vite dev server must never be the thing facing the internet -- it is
# unoptimised, it serves source maps and module graphs, and it is not written
# to be hardened. The two targets exist so the local flow stays convenient
# without that convenience reaching production by accident.

FROM node:20-slim AS dev
WORKDIR /srv
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev"]


FROM node:20-slim AS build
WORKDIR /srv
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Vite inlines env vars at BUILD time, so the API's URL has to be known now --
# it cannot be injected when the container starts. Pass it with
# `--build-arg VITE_API_BASE=https://api.example.org`.
ARG VITE_API_BASE
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build


FROM nginx:1.27-alpine
COPY --from=build /srv/dist /usr/share/nginx/html
# A single-page app: unknown paths are routes, not missing files, so they fall
# through to index.html rather than 404ing.
RUN printf 'server {\n\
  listen 80;\n\
  server_tokens off;\n\
  root /usr/share/nginx/html;\n\
  index index.html;\n\
  location / { try_files $uri $uri/ /index.html; }\n\
  location ~* \\.(js|css|woff2?|png|svg|jpg)$ { expires 1y; add_header Cache-Control "public, immutable"; }\n\
}\n' > /etc/nginx/conf.d/default.conf
EXPOSE 80
