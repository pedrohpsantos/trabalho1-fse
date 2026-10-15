PYTHON ?= python
CENARIO ?= 0

.PHONY: help install test lint format run-all-mock run-bancada run-central-mock run-cabine-mock graficos clean

help:
	@echo "make install          - instala dependências (requirements.txt)"
	@echo "make test             - pytest -v"
	@echo "make lint             - ruff check"
	@echo "make run-all-mock     - bancada + Central + 3 Distribuidos (CENARIO=1|2|3 opcional)"
	@echo "make run-bancada      - somente a bancada virtual"
	@echo "make run-central-mock - Central contra a bancada virtual"
	@echo "make run-cabine-mock N=1 - Distribuido da cabine N contra a bancada virtual"
	@echo "make graficos         - gera os graficos de sintonia do PID em docs/graficos"
	@echo "Hardware: python -m elevador.central --config config/central.json"
	@echo "          python -m elevador.cabine  --config config/cabineN.json"

install:
	$(PYTHON) -m pip install -r requirements.txt

test:
	$(PYTHON) -m pytest -v

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

run-all-mock:
	$(PYTHON) scripts/run_all_mock.py --cenario $(CENARIO)

run-bancada:
	$(PYTHON) -m elevador.sim --cenario $(CENARIO)

run-central-mock:
	$(PYTHON) -m elevador.central --config config/central.json --mock

run-cabine-mock:
	$(PYTHON) -m elevador.cabine --config config/cabine$(N).json --mock

graficos:
	$(PYTHON) scripts/gerar_graficos_pid.py

clean:
	rm -rf .pytest_cache .ruff_cache dados
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
