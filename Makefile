# Everything runs in containers (Podman). The host only needs podman (+ podman-compose for `make up`).
RUN       = podman run --rm --security-opt label=disable -v "$(CURDIR)":/work -w /work  # label=disable: SELinux hosts (Fedora, RHEL) allow the mount
HELM_IMG  = docker.io/alpine/helm:latest
KUBECONF  = ghcr.io/yannh/kubeconform:latest
UV_IMG    = ghcr.io/astral-sh/uv:python3.12-bookworm-slim
TEST_IMG  = localhost/sif-test:latest
NODE_IMG  = docker.io/library/node:22-alpine
VISUAL_IMG = mcr.microsoft.com/playwright:v1.52.0-noble
3D        = components/3d-view
NODE_RUN  = $(RUN) -v sif-3d-view-node-modules:/work/$(3D)/node_modules -w /work/$(3D) $(NODE_IMG)

.PHONY: lint-charts test test-py test-3d test-3d-visual build-3d up down logs init seed unseed e2e backup restore

lint-charts:
	$(RUN) --entrypoint sh $(HELM_IMG) -c 'for c in components/*/chart; do helm lint $$c || exit 1; done'
	$(RUN) --entrypoint sh $(HELM_IMG) -c 'for c in components/*/chart; do helm template t $$c; done' \
	  | $(RUN) -i $(KUBECONF) -strict -ignore-missing-schemas -summary -

test: test-py test-3d

PG_IMG    = docker.io/pgvector/pgvector:pg17

# Python tests run against a throwaway Postgres container on a private network.
test-py:
	@podman image exists $(TEST_IMG) || podman build -q -t $(TEST_IMG) -f tests/Dockerfile tests >/dev/null
	-@podman network create sif-test >/dev/null 2>&1
	-@podman rm -f sif-test-pg >/dev/null 2>&1
	-@podman rm -f sif-test-temporal >/dev/null 2>&1
	podman run -d --rm --name sif-test-temporal --network sif-test docker.io/temporalio/temporal:1.9.1 server start-dev --ip 0.0.0.0 --headless >/dev/null
	podman run -d --rm --name sif-test-pg --network sif-test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=sif $(PG_IMG) >/dev/null
	@for i in $$(seq 1 30); do podman exec sif-test-pg pg_isready -U postgres -d sif >/dev/null 2>&1 && break; sleep 1; done
	@for i in $$(seq 1 40); do podman exec sif-test-temporal temporal operator cluster health >/dev/null 2>&1 && break; sleep 1; done
	@$(RUN) --network sif-test -e TEMPORAL_ADDRESS=sif-test-temporal:7233 -e DATABASE_URL=postgresql://postgres:test@sif-test-pg:5432/sif \
	  -e UV_PROJECT_ENVIRONMENT=/opt/venv -v sif-uv-venv:/opt/venv $(TEST_IMG) \
	  sh -c 'uv sync --all-packages && uv run pytest -q'; rc=$$?; podman stop sif-test-pg sif-test-temporal >/dev/null 2>&1; exit $$rc

test-3d:
	$(NODE_RUN) sh -c 'npm install --no-audit --no-fund && npx vitest run'

# Screenshot tests of the 3D view's panels, list and dialogs, in the Playwright container (its own node_modules: the image is glibc, the
# dev server's is Alpine). After an intended visual change: `make test-3d-visual UPDATE=1`, then review the new PNGs before committing.
test-3d-visual:
	$(RUN) -v sif-3d-visual-node-modules:/work/$(3D)/node_modules -w /work/$(3D) -e NODE_PATH=/work/$(3D)/node_modules $(VISUAL_IMG) \
	  sh -c 'npm install --no-audit --no-fund && npx playwright test -c ../../tests/3d-view/visual $(if $(UPDATE),--update-snapshots=all,)'

build-3d:
	$(NODE_RUN) sh -c 'npm install --no-audit --no-fund && npm run build'

# Re-running replaces what is running: the stack's containers and any leftover agent sandboxes are stopped first. Volumes (the database)
# are kept; use `podman-compose down -v` to start from empty.
up:
	-podman-compose down --remove-orphans >/dev/null 2>&1
	-podman ps -aq --filter name=sif-sandbox- | xargs podman rm -f >/dev/null 2>&1
	podman-compose up -d --build
	@echo "Dashboard: http://localhost:3000   3D view: http://localhost:5173   API: http://localhost:8000/docs"

down:
	podman-compose down

# Follows the services' output: `make logs` for all of them, `make logs s=jira-connector` for one (Ctrl-C stops following, not the stack).
logs:
	podman-compose logs -f --tail 50 $(s)

# One-time: append generated secrets to .env (existing values are never overwritten).
init:
	@touch .env && chmod 600 .env
	@grep -q '^SIF_INGEST_TOKEN=.' .env || echo "SIF_INGEST_TOKEN=$$(openssl rand -hex 24)" >> .env
	@grep -q '^SIF_INTERNAL_TOKEN=.' .env || echo "SIF_INTERNAL_TOKEN=$$(openssl rand -hex 24)" >> .env
	@grep -q '^SIF_DASHBOARD_TOKEN=.' .env || echo "SIF_DASHBOARD_TOKEN=$$(openssl rand -hex 24)" >> .env
	@grep -q '^SIF_SECRET_KEY=.' .env || echo "SIF_SECRET_KEY=$$(openssl rand -base64 32 | tr '+/' '-_')" >> .env
	@grep -q '^POSTGRES_PASSWORD=.' .env || echo "POSTGRES_PASSWORD=$$(openssl rand -hex 16)" >> .env
	@grep -q '^PODMAN_SOCKET=.' .env || echo "PODMAN_SOCKET=$$(podman info --format '{{.Host.RemoteSocket.Path}}' | sed 's|^unix://||')" >> .env  # where sandboxes are started: differs per OS and user
	@echo ".env ready (values not shown)"

# Demo rows are marked demo=true and only appear on the dashboard with ?demo=1.
seed:
	$(RUN) --network sif_default -e DATABASE_URL=postgresql://sif:$${POSTGRES_PASSWORD:-sif-local-dev}@postgres:5432/sif \
	  -e UV_PROJECT_ENVIRONMENT=/opt/venv -v sif-uv-venv:/opt/venv $(UV_IMG) \
	  sh -c 'uv sync --all-packages -q && uv run python components/portal-api/tools/seed.py'

unseed:
	$(RUN) --network sif_default -e DATABASE_URL=postgresql://sif:$${POSTGRES_PASSWORD:-sif-local-dev}@postgres:5432/sif \
	  -e UV_PROJECT_ENVIRONMENT=/opt/venv -v sif-uv-venv:/opt/venv $(UV_IMG) \
	  sh -c 'uv sync --all-packages -q && uv run python components/portal-api/tools/seed.py --clear'

# Full-system test with mock Jira/GitHub/LLM, as a separate Compose project: no published ports, its own volumes and sandbox network,
# so it never touches a running dev stack. Takes a few minutes the first time (images build).
E2E = podman-compose -p sife2e -f compose.e2e.generated.yaml
e2e:
	$(RUN) $(UV_IMG) sh -c 'uv run --no-project --with pyyaml python tests/e2e/prepare.py compose.yaml compose.e2e.yaml compose.e2e.generated.yaml'
	@I=$$(openssl rand -hex 12); T=$$(openssl rand -hex 12); \
	  export SIF_INGEST_TOKEN=$$I SIF_INTERNAL_TOKEN=$$T POSTGRES_PASSWORD=e2e-$$I SIF_SECRET_KEY=$$(openssl rand -base64 32 | tr "+/" "-_"); \
	  $(E2E) up -d --build >/dev/null 2>&1 || { $(E2E) up -d --build 2>&1 | tail -20; exit 1; }; \
	  podman run --rm --network sife2e_default -e SIF_INGEST_TOKEN=$$I -e SIF_INTERNAL_TOKEN=$$T -v "$(CURDIR)/tests/e2e":/e2e:ro $(UV_IMG) \
	    sh -c 'uv run --no-project --with httpx python /e2e/run_e2e.py'; rc=$$?; \
	  for t in PAY-1 PAY-2 PAY-3 PAY-4 PAY-5 PAY-6; do podman rm -f sif-sandbox-$$(echo $$t | tr A-Z a-z) >/dev/null 2>&1; done; \
	  $(E2E) down -v >/dev/null 2>&1; podman network rm -f sife2e-sandboxes >/dev/null 2>&1; exit $$rc

# Backups: a compressed pg_dump of the whole database (tasks, lessons, team knowledge, audit log, encrypted connection secrets) into
# backups/. Keep SIF_SECRET_KEY with it, separately: without the key the saved connection tokens cannot be read.
# Restore into a running stack with `make restore FILE=backups/sif-....dump` (it replaces what is there).
backup:
	@mkdir -p backups && chmod 700 backups
	@F=backups/sif-$$(date +%Y%m%d-%H%M%S).dump; podman exec sif_postgres_1 pg_dump -U sif -d sif -Fc > $$F && echo "wrote $$F"

restore:
	@test -n "$(FILE)" || (echo "usage: make restore FILE=backups/sif-....dump" && exit 1)
	podman exec -i sif_postgres_1 pg_restore -U sif -d sif --clean --if-exists --no-owner < $(FILE)
