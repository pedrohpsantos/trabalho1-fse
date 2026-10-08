# Makefile para a Entrega 1 - Controle da Cabine 1 (Python)
# Disciplina: Fundamentos de Sistemas Embarcados (2026/2)

.PHONY: all run test rpi widget diagnostico install clean help

PYTHON ?= python

# Target padrão
all: test

# Instala as dependências do projeto
install:
	$(PYTHON) -m pip install -r requirements.txt

# Executa o sistema no computador de desenvolvimento (Modo Simulado / Mock)
run:
	$(PYTHON) main.py --mock

# Executa a suíte de testes unitários automatizados
test:
	$(PYTHON) -m pytest tests -v

# Executa testes com o módulo nativo unittest
test-unittest:
	$(PYTHON) -m unittest discover -s tests -p "test_*.py" -v

# Executa o sistema na Raspberry Pi com pinos reais (RPi.GPIO)
rpi:
	$(PYTHON) main.py --rpi

# Executa com o preset de pinos do Widget da Bancada
widget:
	$(PYTHON) main.py --widget

# Executa a ferramenta de diagnóstico físico de pinos na Raspberry Pi
diagnostico:
	$(PYTHON) diagnostico.py

# Limpa caches e arquivos temporários do Python
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

# Exibe a lista de comandos disponíveis
help:
	@echo "Comandos disponíveis no Makefile (Python):"
	@echo "  make install       - Instala as dependências via requirements.txt"
	@echo "  make run           - Executa o sistema localmente em modo simulado (Mock)"
	@echo "  make test          - Executa a suíte de testes com pytest"
	@echo "  make test-unittest - Executa os testes usando o módulo unittest nativo"
	@echo "  make rpi           - Executa na Raspberry Pi com o driver real (RPi.GPIO)"
	@echo "  make widget        - Executa com o preset de pinos do Widget do ThingsBoard"
	@echo "  make diagnostico   - Executa a ferramenta de diagnóstico de pinos na Raspberry Pi"
	@echo "  make clean         - Remove pastas de cache (__pycache__, .pytest_cache)"
