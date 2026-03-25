# Makefile for managing the Alerts Service application

# Declare phony targets to avoid conflicts with files of the same name
.PHONY: up stop clean prod test test-api

up:
# Start the development environment using Docker Compose
# - Uses Dockerfile.dev with live code reloading
# - Mounts source code, scripts, and data directories
# - Downloads and simplifies IGN geo layers on first run
# - Access: http://localhost:8080/docs
	docker compose -f docker-compose-dev.yaml up --build

down:
# Stop all running containers
	docker compose down
	docker compose -f docker-compose-dev.yaml down

clean:
# Stop containers and remove volumes
	docker compose -f docker-compose-dev.yaml down -v

prod:
# Start production environment
	docker compose up --build

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

precommit:
	pre-commit run --all-files
