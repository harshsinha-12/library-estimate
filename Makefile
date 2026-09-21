.PHONY: check ios-build ios-project lint test run-backend

check: lint test

lint:
	python3 -m ruff check backend cv
	python3 -m compileall -q backend cv

test:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest

run-backend:
	python3 -m uvicorn backend.app.main:production_app --factory --reload --host 0.0.0.0 --port 8000

ios-project:
	cd ios/LibrarySurvey && xcodegen generate

ios-build: ios-project
	cd ios/LibrarySurvey && xcodebuild -project LibrarySurvey.xcodeproj -scheme LibrarySurvey -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' -derivedDataPath /tmp/library-roomplan-derived CODE_SIGNING_ALLOWED=NO build
