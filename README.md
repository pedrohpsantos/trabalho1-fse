# Entrega 1 — Módulo da GPIO: Controle de Uma Cabine de Elevador (Python)

Trabalho 1 — Entrega 1 da disciplina de **Fundamentos de Sistemas Embarcados (2026/2)**  
Faculdade do Gama — Universidade de Brasília (FGA-UnB)

---

## 1. Visão Geral

Esta solução implementa o módulo de controle da **Cabine 1** de um simulador de elevador com modelo reduzido de 3 andares (0, 1 e 2), utilizando a linguagem de programação **Python 3**. O projeto atende rigorosamente a todos os requisitos de baixo e alto nível especificados no [`Entrega_1.md`](../Entrega_1.md) e está sincronizado com os ajustes validados na bancada física (Bancada 36):

- **Saídas Digitais on/off (`DIR1` e `DIR2`)**: Controle dos estados Livre, Subir, Descer e Freio.
- **Saída PWM (1 kHz)**: Modulação de potência do motor de tração com rampa de partida para superar o atrito estático ($\ge 20\%$).
- **Entrada com Interrupção (Encoder em Quadratura 4×)**: Leitura nos pinos `ENC_A` e `ENC_B` por interrupção de hardware em ambas as bordas (`GPIO.BOTH`), decodificação por tabela de transição com polaridade da bancada (subida incrementa, descida decrementa) e contador de 32 bits com sinal.
- **Entrada on/off com Debounce (Cortina de Luz)**: Tratamento do sinal com janela de debounce temporal (40 ms) e notificação imediata de obstrução e liberação.
- **Sensor de Andar (Bandeirola)**: Leitura de ambas as bordas (entrada e saída), registrando a posição do encoder, calculando o centro estimado pela média e o erro relativo ao andar nominal mais próximo (0, 3000 ou 6000 mm).
- **Controle de Nivelamento**: Parada suave dentro da faixa de tolerância de **$\pm 10\text{ mm}$** com acionamento do freio elétrico.
- **Proteção de Fim de Curso**: Bloqueio de comandos fora da faixa de $0$ a $6000\text{ mm}$.
- **Tratamento de SIGINT (`Ctrl+C`)**: Zeramento imediato do PWM, acionamento do freio elétrico e liberação segura dos pinos de GPIO.
- **Ausência de Busy-Wait**: Todas as esperas e temporizações utilizam sleeps e filas de eventos não-bloqueantes.

---

## 2. Mapeamento de Pinos da Cabine 1 (Raspberry Pi — Pinos BCM)

| Sinal | Função | GPIO RPi (BCM) | Direção | Modalidade |
|:---|:---|:---:|:---:|:---|
| `PWM` | Potência do motor de tração (1 kHz) | **13** | Saída | PWM (0–100%) |
| `DIR1` | Direção 1 | **22** | Saída | Digital on/off |
| `DIR2` | Direção 2 | **23** | Saída | Digital on/off |
| `ENC_A` | Canal A do Encoder | **20** | Entrada | Interrupção (ambas as bordas) |
| `ENC_B` | Canal B do Encoder | **21** | Entrada | Interrupção (ambas as bordas) |
| `CORTINA` | Cortina de luz da porta | **26** | Entrada | Digital on/off (debounce 40ms) |
| `SENSOR_ANDAR` | Sensor de andar (bandeirola) | **0** | Entrada | Interrupção (ambas as bordas) |

> ℹ️ **Presets de Execução**:
> - Padrão (**Bancada 36**): `PWM=13, DIR1=22, DIR2=23, ENC=20/21, CORTINA=26, SENSOR=0`.
> - **Widget Alternativo**: Se a bancada estiver usando a configuração alternativa do ThingsBoard (`DIR1=17, DIR2=27, SENSOR=11`), execute com `--widget` ou `--bancada`.

### Tabela de Direção do Motor

| Ação | `DIR1` (GPIO 22) | `DIR2` (GPIO 23) |
|:---|:---:|:---:|
| **Livre** | 0 | 0 |
| **Subir** | 1 | 0 |
| **Descer** | 0 | 1 |
| **Freio** | 1 | 1 |

---

## 3. Arquitetura de Software em Python

```
entrega1_python/
├── Makefile                     # Automação de execução, testes e diagnóstico
├── README.md                    # Documentação do projeto
├── requirements.txt             # Dependências (RPi.GPIO, pytest)
├── main.py                      # Ponto de entrada raiz
├── diagnostico.py               # Utilitário de teste e diagnóstico físico de pinos
├── tests/
│   ├── __init__.py
│   └── test_motion.py           # Testes automatizados (bandeirola, rampas, limites, física)
└── src/
    ├── __init__.py              # Inicialização de pacote e configuração segura de encoding UTF-8
    ├── main.py                  # Ponto de entrada interno, tratamento de SIGINT e threads
    ├── config.py                # Constantes de pinos, física, andares e limites
    ├── hal/                     # Camada de Abstração de Hardware (HAL)
    │   ├── __init__.py
    │   ├── base.py              # Classe abstrata ElevatorHardware, tipos de eventos e QUADRATURE_TABLE
    │   ├── mock.py              # Driver simulado com física integrada para testes locais
    │   └── rpi.py               # Driver real para Raspberry Pi com RPi.GPIO
    ├── sensors/                 # Tratamento de sinais dos sensores
    │   ├── __init__.py
    │   └── bandeirola.py        # Detector de bordas, estimador de centro e cálculo de erro
    ├── controller/              # Lógica de controle de alto nível
    │   ├── __init__.py
    │   ├── elevator.py          # Máquina de estados da cabine e despacho de eventos
    │   └── motion.py            # Gerador de perfil de movimento (rampas de aceleração/desaceleração)
    └── cli/                     # Interface de terminal (CLI)
        ├── __init__.py
        └── cli.py               # Interpretador de comandos interativos
```

---

## 4. Instruções de Instalação e Execução

### 4.1 Pré-requisitos e Dependências

Instale as dependências com:

```bash
pip install -r requirements.txt
```

### 4.2 Execução na Raspberry Pi (Hardware Real)

```bash
# Executar na Bancada 36 (padrão)
python main.py --rpi

# Ou se for necessário usar os pinos do widget alternativo
python main.py --rpi --widget

# Ou via Makefile
make rpi
```

### 4.3 Diagnóstico de Pinos Físicos na Raspberry Pi

Caso queira validar os pinos da bancada antes da execução completa:

```bash
python diagnostico.py

# Ou via Makefile
make diagnostico
```

### 4.4 Execução Local no Computador de Desenvolvimento (Modo Simulado)

O programa detecta automaticamente se não estiver em uma Raspberry Pi e executa no modo simulado com cinemática integrada:

```bash
python main.py --mock

# Ou simplesmente
python main.py

# Ou via Makefile
make run
```

### 4.5 Execução dos Testes Automatizados

```bash
pytest tests -v

# Ou via Makefile
make test
```

---

## 5. Interface Interativa de Terminal (CLI)

```
╔══════════════════════════════════════════════════════════════╗
║        SISTEMA DE CONTROLE DE ELEVADORES — FSE 2026/2        ║
║                   ENTREGA 1 — CABINE 1 (PYTHON)             ║
╠══════════════════════════════════════════════════════════════╣
║ Comandos disponíveis:                                        ║
║   andar <0|1|2>           - Move para o andar desejado       ║
║   motor <dir> <duty>      - dir: livre | subir | descer | freio║
║                             duty: 0 a 100                    ║
║   status                  - Exibe estado dos sensores e motor║
║   parar                   - Para o motor e aciona o freio    ║
║   ajuda                   - Exibe esta mensagem de ajuda     ║
║   sair                    - Encerra o programa graciosamente ║
╚══════════════════════════════════════════════════════════════╝
```

Exemplos:
- `andar 1`
- `motor subir 40`
- `status`
- `parar`
- `sair`
