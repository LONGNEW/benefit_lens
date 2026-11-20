PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
EXPANSION_OUTPUT ?= pipeline/data/dictionary-expansion/current
.PHONY: init install doctor db-up db-stop schema sample-db demo status test smoke patterns experience-originals backup verify restore
init:
	python3 tools/project.py init
install:
	python3 -m venv --without-pip .venv
	python3 -m pip --python .venv/bin/python install -r pipeline/requirements.lock -e pipeline
doctor db-up db-stop schema demo status:
	$(PYTHON) tools/project.py $@
test:
	PYTHONPATH=tools:tools/tests:pipeline/src $(PYTHON) -m pytest pipeline/tests tools/tests tests -q --import-mode=importlib
smoke:
	$(PYTHON) tools/smoke_demo.py
sample-db:
	$(PYTHON) tools/sample_data.py
patterns:
	$(PYTHON) tools/extract_patterns.py --collection "$(COLLECTION)"
experience-originals:
	$(PYTHON) tools/extend_experiences.py --collection "$(COLLECTION)"
backup:
	$(PYTHON) tools/data_bundle.py backup --source-root "$(SOURCE)" --source-container "$(CONTAINER)" --output "$(BUNDLE)"
verify:
	$(PYTHON) tools/data_bundle.py verify --bundle "$(BUNDLE)"
restore:
	$(PYTHON) tools/data_bundle.py restore --bundle "$(BUNDLE)"
.PHONY: dictionary-expand
dictionary-expand:
	$(PYTHON) tools/run_dictionary_expansion.py --output "$(EXPANSION_OUTPUT)"
.PHONY: pipeline-replay
pipeline-replay:
	$(PYTHON) tools/run_pipeline_demo.py $(if $(COLLECTION),--collection "$(COLLECTION)",)
