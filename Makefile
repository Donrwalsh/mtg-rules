# Eval harness targets (see README "Evals"). Every target wraps `python -m mtg_evals`,
# so each also works without make. Pass-through variables:
#   EXP=<experiment>   TAG="tag1 tag2"   ID="case-id other-id"   ARGS="--fresh-answers"

PYTHON ?= python
EVALS  := $(PYTHON) -m mtg_evals
MODE   ?= retrieval
FILTER  = $(if $(EXP),--exp $(EXP)) $(foreach t,$(TAG),--tag $(t)) $(foreach i,$(ID),--id $(i)) $(ARGS)
SELECT  = $(foreach t,$(TAG),--tag $(t)) $(foreach i,$(ID),--id $(i))

.PHONY: eval eval-full eval-test eval-baseline eval-compare eval-show eval-sweep eval-validate

eval:
	$(EVALS) run --mode retrieval --split dev $(FILTER)

eval-full:
	$(EVALS) run --mode full --split dev $(FILTER)

# Both modes; the full run still happens if retrieval regressed.
eval-test:
	$(EVALS) run --mode retrieval --split test $(FILTER); r=$$?; \
	$(EVALS) run --mode full --split test $(FILTER); f=$$?; \
	exit $$(( r > f ? r : f ))

eval-baseline:
	$(EVALS) baseline --mode $(MODE)

eval-compare:
	$(EVALS) compare $(A) $(B)

# Report for the latest run, or RUN=<run file or name>.
eval-show:
	$(EVALS) show $(RUN)

eval-sweep:
	$(EVALS) sweep $(EXPS) --mode $(MODE) $(SELECT)

eval-validate:
	$(EVALS) validate

# Copy the local Qdrant collection and parsed data into the production stack
# (see README "Production deployment"). HOST=root@your-server
.PHONY: sync-prod
sync-prod:
	$(PYTHON) deploy/sync_data.py --host $(HOST) $(if $(PROJECT),--project $(PROJECT))
