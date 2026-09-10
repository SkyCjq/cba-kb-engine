PYTHON ?= .venv/bin/python
export PYTHONPATH := src:legacy
.PHONY: doctor auth test validate build pull extract facts accept registry plan publish verify restore sync
doctor:
	$(PYTHON) -m cba_kb.cli doctor
auth:
	$(PYTHON) -m cba_kb.cli auth
test:
	$(PYTHON) -m pytest -q
validate:
	$(PYTHON) -m cba_kb.cli validate --master "$(MASTER)"
build:
	$(PYTHON) -m cba_kb.cli build --master "$(MASTER)" --output "$(OUTPUT)" --release-id "$(RELEASE_ID)"
pull:
	$(PYTHON) -m cba_kb.cli pull --output "$(OUTPUT)"
extract:
	$(PYTHON) -m cba_kb.cli extract $(ARGS)
facts:
	$(PYTHON) -m cba_kb.cli facts $(ARGS)
accept:
	$(PYTHON) scripts/verify_v1_5_1_sources.py $(ARGS)
registry:
	$(PYTHON) scripts/propose_source_registry.py $(ARGS)
plan:
	$(PYTHON) -m cba_kb.cli plan $(ARGS)
publish:
	$(PYTHON) -m cba_kb.cli publish $(ARGS)
verify:
	$(PYTHON) -m cba_kb.cli verify $(ARGS)
restore:
	$(PYTHON) -m cba_kb.cli restore $(ARGS)
sync:
	@echo 'Legacy sync is disabled. Use pull, validate, build, plan, publish, verify.'
	@exit 1
