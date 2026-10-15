PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
.PHONY: init install doctor db-up db-stop schema sample-db demo status test smoke backup verify restore
init:
	python3 tools/project.py init
install:
	python3 -m venv --without-pip .venv
	python3 -m pip --python .venv/bin/python install -r pipeline/requirements.lock -e pipeline
doctor db-up db-stop schema demo status:
	$(PYTHON) tools/project.py $@
test:
	$(PYTHON) -m pytest pipeline/tests tools/tests -q
smoke:
	$(PYTHON) tools/smoke_demo.py
sample-db:
	$(PYTHON) tools/sample_data.py
backup:
	$(PYTHON) tools/data_bundle.py backup --source-root "$(SOURCE)" --source-container "$(CONTAINER)" --output "$(BUNDLE)"
verify:
	$(PYTHON) tools/data_bundle.py verify --bundle "$(BUNDLE)"
restore:
	$(PYTHON) tools/data_bundle.py restore --bundle "$(BUNDLE)"
