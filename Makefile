# --- Variables ---
BACKEND_DIR = backend
FRONTEND_DIR = frontend
VENV = venv
PYTHON = $(VENV)/bin/python
UVICORN = $(VENV)/bin/uvicorn

.PHONY: help setup api ui build deploy clean bootstrap release

help: ## Show this help message
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

# --- Core Commands ---

setup: ## Install all dependencies for backend and frontend
	@echo "📦 Setting up project..."
	@cd $(BACKEND_DIR) && rm -rf venv && python3 -m venv venv
	@cd $(BACKEND_DIR) && source venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt
	@cd $(FRONTEND_DIR) && npm install

api: ## Start backend development server
	@echo "🚀 Starting API Server..."
	@cd $(BACKEND_DIR) && $(UVICORN) main:app --reload --port 8000

ui: ## Start frontend development server
	@echo "🚀 Starting UI Dev Server..."
	@cd $(FRONTEND_DIR) && npm run dev

build: ## Build frontend assets
	@echo "🏗️  Building UI..."
	@cd $(FRONTEND_DIR) && npm run build

deploy: ## Deploy backend (auto-deploys via Railway on git push to main)
	@echo "☁️  Railway auto-deploys on push to main branch."
	@echo "    To deploy manually: railway up (requires Railway CLI)"

clean: ## Remove temporary files and caches
	@echo "🧹 Cleaning up..."
	@find . -type d -name "__pycache__" -exec rm -rf {} +
	@rm -rf $(FRONTEND_DIR)/dist

# --- Workflows ---

bootstrap: clean setup ## Fresh start: Clean and install dependencies
	@echo "✨ Environment bootstrapped!"

release: clean build ## Production ready: Clean and build
	@echo "🚀 Frontend built. Push to main to deploy via Railway + Vercel."
