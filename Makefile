# Makefile for managing the Alerts Service application

# Declare phony targets to avoid conflicts with files of the same name
.PHONY: install dev local test stop logs clean

install:
	pip install poetry
	poetry install

dev:
# Start the development environment using Docker Compose
# - Uses Dockerfile.dev with live code reloading
# - Mounts source code, scripts, and data directories
# - Downloads and simplifies IGN geo layers on first run
# - Access: http://localhost:8080/docs
	docker compose -f docker-compose-dev.yaml up --build

dev-detached:
# Start development environment in background
	docker compose -f docker-compose-dev.yaml up -d --build

stop:
# Stop all running containers
	docker compose -f docker-compose-dev.yaml down

logs:
# Follow logs from the development container
	docker compose -f docker-compose-dev.yaml logs -f

clean:
# Stop containers and remove volumes
	docker compose -f docker-compose-dev.yaml down -v

prod:
# Start production environment
	docker compose up -d --build

prod-stop:
# Stop production environment
	docker compose down

local:
	cd ./src && uvicorn main:app --host 0.0.0.0 --port 8080 --reload

test:
# Build the test Docker image and run the tests
# - Uses Dockerfile.run_test which installs dev dependencies
# - Runs pytest with coverage reporting and JUnit XML output
# - Mounts ./reports to persist test reports and coverage HTML
	docker build . -f Dockerfile.run_test -t alerts-service-test && docker run --rm -v ./reports/:/app/reports alerts-service-test

test-api:
# Run API integration tests
# Prerequisites: Service must be running (make dev)
# Tests both simplified and full resolution endpoints
# Results saved in tests/ directory as GeoJSON files
	@echo "Running API integration tests..."
	@cd tests && python3 test_alerts_api.py
