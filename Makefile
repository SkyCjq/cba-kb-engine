PYTHON ?= .venv/bin/python
export PYTHONPATH := src:legacy
INSTANCE_ARG = $(if $(INSTANCE_ROOT),--instance-root "$(INSTANCE_ROOT)",)
.PHONY: doctor auth test validate build pull extract facts domain watch document-ingest document-batch verify-v1.5.2 sync-v1.5.2 prepare-v1.5.2 reconcile-v1.5.3 prepare-v1.5.3 prepare-v1.5.3-production accept registry plan publish verify restore sync
doctor:
	$(PYTHON) -m cba_kb.cli $(INSTANCE_ARG) doctor
auth:
	$(PYTHON) -m cba_kb.cli $(INSTANCE_ARG) auth
test:
	$(PYTHON) -m pytest -q
validate:
	$(PYTHON) -m cba_kb.cli validate --master "$(MASTER)"
build:
	$(PYTHON) -m cba_kb.cli build --master "$(MASTER)" --output "$(OUTPUT)" --release-id "$(RELEASE_ID)"
pull:
	$(PYTHON) -m cba_kb.cli $(INSTANCE_ARG) pull --output "$(OUTPUT)"
extract:
	$(PYTHON) -m cba_kb.cli extract $(ARGS)
facts:
	$(PYTHON) -m cba_kb.cli facts $(ARGS)
domain:
	$(PYTHON) -m cba_kb.cli domain $(ARGS)
watch:
	$(PYTHON) -m cba_kb.cli $(INSTANCE_ARG) watch $(ARGS)
document-ingest:
	$(PYTHON) -m cba_kb.cli $(INSTANCE_ARG) document-ingest $(ARGS)
document-batch:
	$(PYTHON) -m cba_kb.cli $(INSTANCE_ARG) document-batch $(ARGS)
verify-v1.5.2:
	$(PYTHON) scripts/verify_v1_5_2_sources.py $(ARGS)
sync-v1.5.2:
	$(PYTHON) scripts/sync_v1_5_2_candidate.py $(ARGS)
prepare-v1.5.2:
	$(PYTHON) scripts/prepare_v1_5_2.py $(ARGS)
reconcile-v1.5.3:
	$(PYTHON) scripts/reconcile_v1_5_3.py $(ARGS)
prepare-v1.5.3:
	$(PYTHON) scripts/prepare_v1_5_3.py $(ARGS)
prepare-v1.5.3-production:
	$(PYTHON) scripts/prepare_v1_5_3_production.py $(ARGS)
accept:
	$(PYTHON) scripts/verify_v1_5_1_sources.py $(ARGS)
registry:
	$(PYTHON) scripts/propose_source_registry.py $(ARGS)
plan:
	$(PYTHON) -m cba_kb.cli $(INSTANCE_ARG) plan $(ARGS)
publish:
	$(PYTHON) -m cba_kb.cli $(INSTANCE_ARG) publish $(ARGS)
verify:
	$(PYTHON) -m cba_kb.cli $(INSTANCE_ARG) verify $(ARGS)
restore:
	$(PYTHON) -m cba_kb.cli $(INSTANCE_ARG) restore $(ARGS)
sync:
	@echo 'Legacy sync is disabled. Use pull, validate, build, plan, publish, verify.'
	@exit 1
