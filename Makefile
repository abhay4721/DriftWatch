.PHONY: setup up down clean logs test lint

setup:
	powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1

up:
	docker compose up --build -d

down:
	docker compose down --remove-orphans

clean:
	docker compose down --volumes --remove-orphans

logs:
	docker compose logs -f --tail=100

test:
	python -m pytest backend/tests

lint:
	python -m ruff check backend

