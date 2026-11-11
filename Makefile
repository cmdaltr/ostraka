.PHONY: help env up down logs ps check test test-backend test-db test-frontend e2e test-launcher test-scripts release lint dev-backend dev-frontend

help:
	@echo "make env            write .env with random secrets (refuses to overwrite)"
	@echo "make up | down      start or stop the stack (http://127.0.0.1:24747)"
	@echo "make logs | ps      follow logs, list containers"
	@echo "make check          everything before a commit: lint, unit, database, script and e2e tests"
	@echo "make test           backend and frontend unit tests"
	@echo "make test-db        backend tests against a throwaway Postgres container"
	@echo "make e2e            Playwright against the production build"
	@echo "make test-launcher  the host ostraka command"
	@echo "make test-scripts   install, release, responder client and server setup scripts"
	@echo "make release VERSION=x.y.z [UPLOAD=1]  every package into dist/<version>/"
	@echo "make lint           ruff and tsc"
	@echo "make dev-backend    uvicorn with reload on :6176 (needs Postgres and Redis)"
	@echo "make dev-frontend   Vite on :5173, proxying /api to :6176"

env:
	@test ! -e .env || { echo ".env exists; not overwriting"; exit 1; }
	@sed -e "s|^POSTGRES_PASSWORD=$$|POSTGRES_PASSWORD=$$(openssl rand -hex 24)|" \
	     -e "s|^OSTRAKA_SECRET_KEY=$$|OSTRAKA_SECRET_KEY=$$(openssl rand -hex 32)|" .env.example > .env
	@chmod 600 .env && echo "wrote .env"

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

ps:
	docker compose ps

# All checks run locally; only tagged releases build on GitHub (Ben, 11 November 2026)
check: lint test-db test-frontend test-launcher test-scripts e2e

test: test-backend test-frontend

# A throwaway Postgres on a random loopback port, removed afterwards
test-db:
	@cid=$$(docker run -d --rm --label ostraka.test=1 -e POSTGRES_USER=ostraka -e POSTGRES_PASSWORD=ostraka -e POSTGRES_DB=ostraka -p 127.0.0.1::5432 postgres:16-alpine); \
	trap "docker stop $$cid >/dev/null" EXIT; \
	port=$$(docker port $$cid 5432/tcp | head -1 | cut -d: -f2); \
	until docker exec $$cid pg_isready -U ostraka -q 2>/dev/null; do sleep 1; done; sleep 1; \
	cd backend && OSTRAKA_TEST_DATABASE_URL=postgresql+asyncpg://ostraka:ostraka@127.0.0.1:$$port/ostraka .venv/bin/pytest -q

test-backend:
	cd backend && .venv/bin/pytest -q

test-frontend:
	cd frontend && npm test

# The host ostraka command (Bash), against a fake docker
test-launcher:
	OSTRAKA_TEST_PWSH_DOCKER=1 backend/.venv/bin/pytest -q tests/launcher

# Install and release scripts, the responder client and server setup
test-scripts:
	OSTRAKA_TEST_PWSH_DOCKER=1 backend/.venv/bin/pytest -q tests/install clients/tests deploy/tests

# Every package from one source tarball, into dist/; UPLOAD=1 drafts a GitHub release
release:
	scripts/release.sh --version $(VERSION) $(if $(UPLOAD),--upload)

e2e:
	cd frontend && npx playwright test

lint:
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .
	cd frontend && npm run typecheck

dev-backend:
	cd backend && OSTRAKA_ENV=development .venv/bin/uvicorn ostraka.main:app --reload --port 6176

dev-frontend:
	cd frontend && npm run dev
