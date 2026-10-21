# Single entry point. Targets used by CI (lint, test, synth, scan-ci) only check and never change
# files or AWS resources. Targets that call AWS say so; the paid ones need ALLOW_PAID=1.
PARAMS ?= config/params.json
PY := uv run python

.PHONY: eval-data help init preflight lint test synth deploy verify eval-mock eval-bedrock destroy verify-cleanup scan scan-ci

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/'

init: ## Install dependencies; create config/params.json from the example if missing
	uv sync
	@[ -f config/params.json ] || { cp config/params.example.json config/params.json; echo "created config/params.json"; }

preflight: init ## Read-only: confirm account (last 4 digits) and region, tools, service availability
	./scripts/preflight.sh $(PARAMS)

lint: ## ruff (check only)
	uv run ruff check .

test: ## Unit tests (mock model, no AWS)
	uv run pytest

synth: ## cdk synth + cdk-nag with the example parameters (no AWS calls beyond context lookups)
	npx -y aws-cdk@2 synth -q -c params=config/params.example.json

deploy: init ## Preflight, refuse deletions/replacements unless ALLOW_DESTRUCTIVE=1, then deploy
	./scripts/deploy.sh $(PARAMS)

verify: ## End-to-end check, failure drill and resource-policy deny probe (paid: ALLOW_PAID=1)
	$(PY) scripts/e2e_aws.py --params $(PARAMS) --all --report out/e2e_report.json
	$(PY) scripts/failure_drill_aws.py --params $(PARAMS) --report out/failure_drill.json
	$(PY) scripts/deny_probe_aws.py --params $(PARAMS) --report out/deny_probe.json

eval-data: ## Generate the synthetic evaluation clips (git-ignored) with Pillow and ffmpeg
	$(PY) scripts/gen_synthetic.py

eval-mock: eval-data ## Evaluation and strategy comparison with the mock model (also runs the negative control)
	$(PY) scripts/evaluate.py --mode mock --strategy video
	$(PY) scripts/evaluate.py --mode mock --negative-control
	$(PY) scripts/compare_strategies.py

eval-bedrock: ## Real-model evaluation, 3 runs per configuration (paid: ALLOW_PAID=1)
	$(PY) scripts/compare_strategies.py --mode bedrock --grid d5 --repeat 3 --out out/compare_bedrock_d5.json

destroy: ## Destroy the stack and remove leftovers, with confirmations
	./scripts/cleanup.sh $(PARAMS) infra/bootstrap-asset-keys.txt

verify-cleanup: ## Read-only residual check; exits non-zero if anything is left
	./scripts/verify_cleanup.sh --params $(PARAMS) --bootstrap-keys infra/bootstrap-asset-keys.txt

scan-ci: ## Secret scan of the full history (gitleaks) and of the working tree (detect-secrets)
	gitleaks git . --config .gitleaks.toml --no-banner --redact
	uvx detect-secrets scan --exclude-files '(\.venv|build|cdk\.out|out|uv\.lock|sample-data/eval/videos|\.pytest_cache|\.ruff_cache)/' \
	  | python3 -c 'import json,sys; r=json.load(sys.stdin)["results"]; print("detect-secrets findings:", len(r)); sys.exit(1 if r else 0)'

scan: scan-ci ## Local scans: canary check, workflow lint, dependency audit
	./scripts/secret_canary.sh
	uvx zizmor .github/workflows
	osv-scanner scan --lockfile uv.lock
