# Production 3D view: the built static files behind nginx, which also forwards the event stream to portal-api. No Vite dev server, no
# source, no node_modules. Served under /3d/ so auth-proxy can put it behind the same sign-in as the dashboard (compose profile "sso").
# Build context is the repo root (vite reads the public settings from .env): podman build -f components/3d-view/Dockerfile .
FROM docker.io/library/node:22-alpine AS build
WORKDIR /work/components/3d-view
COPY components/3d-view/package.json components/3d-view/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY components/3d-view/ ./
ENV VITE_BASE=/3d/ VITE_EVENTS_URL=/3d/api/events
RUN npm run build

FROM docker.io/nginxinc/nginx-unprivileged:1.27-alpine
USER root
RUN rm /etc/nginx/conf.d/default.conf
USER nginx
COPY --from=build /work/components/3d-view/dist /usr/share/nginx/html/3d
COPY components/3d-view/nginx.conf.template /etc/nginx/templates/default.conf.template
ENV NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1
EXPOSE 8080
