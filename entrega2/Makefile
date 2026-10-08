PYTHON ?= python

.PHONY: help install run rpi uart test lint format clean

help:
	@echo "Comandos disponíveis:"
	@echo "  make install   - Instala as dependências do requirements.txt"
	@echo "  make run       - Executa a CLI em modo simulador (Mock offline)"
	@echo "  make rpi       - Executa a CLI na porta física da Raspberry Pi (/dev/serial0)"
	@echo "  make uart      - Executa a CLI com UART padrão"
	@echo "  make test      - Executa a suíte de testes automatizados com pytest"
	@echo "  make lint      - Executa verificação de linter com ruff"
	@echo "  make format    - Formata o código com ruff"
	@echo "  make clean     - Remove caches e arquivos temporários"

install:
	$(PYTHON) -m pip install -r requirements.txt

run:
	$(PYTHON) main.py --mock

rpi:
	$(PYTHON) main.py /dev/serial0

uart:
	$(PYTHON) main.py /dev/serial0

test:
	$(PYTHON) -m pytest -v

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

clean:
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
