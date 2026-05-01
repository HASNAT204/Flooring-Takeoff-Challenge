.PHONY: run eval test clean install

PY := python
VENV := .venv

install:
	$(PY) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -r requirements.txt

run:
	$(VENV)/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

eval:
	@if [ -z "$(JOB)" ]; then echo "Usage: make eval JOB=<job_id>"; exit 1; fi
	$(VENV)/bin/python -m evaluator.evaluate \
		--prediction outputs/jobs/$(JOB)/prediction.json \
		--gold gold/gold_takeoff.xlsx \
		--out outputs/jobs/$(JOB)/evaluation.html

test:
	$(VENV)/bin/pytest tests/ -v

clean:
	rm -rf outputs/jobs/* uploads/* __pycache__ */__pycache__ */*/__pycache__ .pytest_cache
