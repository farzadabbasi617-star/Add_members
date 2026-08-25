# ================================================================
# Makefile — دستورات رایج توسعه و استقرار
# ================================================================
.PHONY: help install run test lint check docker clean

help: ## نمایش راهنما
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## نصب وابستگی‌های توسعه
	pip install -r requirements-dev.txt

run: ## اجرای بات
	python bot.py

test: ## اجرای تست‌ها
	python -m pytest tests/ -v

lint: ## تحلیل استاتیک ماژول‌های اصلی
	python -m pyflakes config.py add_engine.py logging_setup.py db.py web_app.py bg_scraper.py account_state.py account_doctor.py

check: ## بررسی سینتکس همه فایل‌ها
	python -m compileall -q .

ci: check lint test ## اجرای کامل چرخه CI به‌صورت محلی

docker: ## ساخت ایمیج داکر
	docker build -t add-members .

clean: ## پاکسازی فایل‌های موقت
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache logs/*.log
