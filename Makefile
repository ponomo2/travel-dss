PYTHON ?= .venv/bin/python
export PYTHONPATH := src

.PHONY: test analyze data schemas demo-data

test:
	$(PYTHON) -m pytest -q

analyze:
	$(PYTHON) scripts/train_forecast.py
	$(PYTHON) scripts/criteria_demo.py
	$(PYTHON) scripts/build_reports.py

data:
	$(PYTHON) scripts/fetch_weather.py
	$(PYTHON) scripts/fetch_osm_map.py
	$(PYTHON) scripts/prepare_catalog.py

schemas:
	$(PYTHON) scripts/export_schemas.py

demo-data:
	$(PYTHON) scripts/build_demo_catalog.py
	$(PYTHON) scripts/build_reports.py

.PHONY: app benchmark docs
app:
	$(PYTHON) -m streamlit run app.py --browser.gatherUsageStats false

benchmark:
	$(PYTHON) scripts/benchmark_system.py

docs:
	$(PYTHON) scripts/build_internals_doc.py
	$(PYTHON) scripts/summarize_system.py

.PHONY: doctor release
doctor:
	$(PYTHON) -m travel_dss.cli doctor

release:
	$(PYTHON) scripts/build_release.py
