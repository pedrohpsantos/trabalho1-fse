# Controle de Cabine de Elevador — Módulo GPIO (Python)

Trabalho 1 — Entrega 1 | Fundamentos de Sistemas Embarcados (2026/2)  
Faculdade do Gama — Universidade de Brasília (FGA-UnB)

---

## 1. Descrição do Sistema

Implementação do módulo de controle da Cabine 1 para modelo reduzido de elevador de 3 andares (0, 1 e 2). O sistema gerencia o acionamento de potência, leitura de sensores e posicionamento em malha fechada via Raspberry Pi:

- **Controle de Tração (Ponte H)**: Sinais digitais `DIR1` e `DIR2` para os estados Livre, Subir, Descer e Freio.
- **Modulação PWM**: Frequência de 1 kHz para controle de velocidade com rampa e piso de partida ($\ge 20\%$) para compensação de atrito estático.
- **Encoder em Quadratura (4x)**: Amostragem por interrupção (`GPIO.BOTH`) nos canais `ENC_A` e `ENC_B`, decodificação por tabela de transição e contador de 32 bits com sinal.
- **Cortina de Luz**: Entrada digital com filtro de debounce temporal (40 ms) e notificação de eventos de obstrução.
- **Sensor de Andar (Bandeirola)**: Detecção de transições de entrada e saída, cálculo do centro geométrico e desvio em relação à cota nominal.
- **Nivelamento**: Tolerância de parada de $\pm 10\text{ mm}$ com corte de PWM e aplicação de freio elétrico.
- **Fim de Curso**: Bloqueio de avanço além da faixa operacional ($0$ a $6000\text{ mm}$).
- **Parada Segura (`SIGINT`)**: Tratamento de sinal `Ctrl+C` com desaceleração, acionamento do freio e liberação de periféricos (`GPIO.cleanup()`).
- **Arquitetura Não-Bloqueante**: Ausência de busy-wait, empregando temporizações cooperativas e filas sincronizadas (`queue.Queue`).

---

## 2. Mapeamento de Pinos da Cabine 1 (Raspberry Pi — BCM)

| Sinal | Função | GPIO RPi (BCM) | Direção | Modalidade |
|:---|:---|:---:|:---:|:---|
| `PWM` | Potência do motor de tração (1 kHz) | **13** | Saída | PWM (0–100%) |
| `DIR1` | Direção 1 | **22** | Saída | Digital on/off |
| `DIR2` | Direção 2 | **23** | Saída | Digital on/off |
| `ENC_A` | Canal A do Encoder | **20** | Entrada | Interrupção (ambas as bordas) |
| `ENC_B` | Canal B do Encoder | **21** | Entrada | Interrupção (ambas as bordas) |
| `CORTINA` | Cortina de luz da porta | **26** | Entrada | Digital on/off (debounce 40ms) |
| `SENSOR_ANDAR` | Sensor de andar (bandeirola) | **0** | Entrada | Interrupção (ambas as bordas) |

> **Presets de Execução**:
> - Padrão (**Bancada**): `PWM=13, DIR1=22, DIR2=23, ENC=20/21, CORTINA=26, SENSOR=0`.
> - **Widget Alternativo**: Se a bancada estiver usando a configuração alternativa do ThingsBoard (`DIR1=17, DIR2=27, SENSOR=11`), execute com `--widget` ou `--bancada`.

### Tabela de Direção do Motor

| Ação | `DIR1` (GPIO 22) | `DIR2` (GPIO 23) |
|:---|:---:|:---:|
| **Livre** | 0 | 0 |
| **Subir** | 1 | 0 |
| **Descer** | 0 | 1 |
| **Freio** | 1 | 1 |

---

## 3. Arquitetura de Software

```
entrega1_python/
├── Makefile                     # Alvos de automação (execução, testes e diagnóstico)
├── README.md                    # Documentação do projeto
├── requirements.txt             # Dependências de execução e teste (RPi.GPIO, pytest)
├── main.py                      # Ponto de entrada raiz
├── diagnostico.py               # Utilitário de validação elétrica de pinos
├── tests/
│   ├── __init__.py
│   └── test_motion.py           # Testes unitários (cinemática, limites e quadratura)
└── src/
    ├── __init__.py              # Inicialização do pacote
    ├── main.py                  # Ponto de entrada interno e orquestração de threads
    ├── config.py                # Mapeamento de hardware e parâmetros físicos
    ├── hal/                     # Camada de Abstração de Hardware
    │   ├── __init__.py
    │   ├── base.py              # Interface abstrata e tabela de quadratura 4x
    │   ├── mock.py              # Driver simulador com cinemática local
    │   └── rpi.py               # Driver de hardware para Raspberry Pi (RPi.GPIO)
    ├── sensors/                 # Tratamento de sinais dos sensores
    │   ├── __init__.py
    │   └── bandeirola.py        # Processamento de bordas e cálculo de centro
    ├── controller/              # Lógica de controle e cinemática
    │   ├── __init__.py
    │   ├── elevator.py          # Máquina de estados e sincronização de eventos
    │   └── motion.py            # Planejador de trajetórias e rampas de aceleração
    └── cli/                     # Interface de terminal
        ├── __init__.py
        └── cli.py               # Interpretador de comandos
```

---

## 4. Instruções de Instalação e Execução

### 4.1 Instalação de Dependências

```bash
pip install -r requirements.txt
```

### 4.2 Execução na Raspberry Pi (Hardware Real)

```bash
# Execução na Bancada 
python main.py --rpi

# Execução com pinagem do widget ThingsBoard
python main.py --rpi --widget

# Via Makefile
make rpi
```

### 4.3 Diagnóstico de Pinos GPIO

Para verificação do estado elétrico dos pinos antes da operação:

```bash
python diagnostico.py

# Via Makefile
make diagnostico
```

### 4.4 Execução em Modo Simulado (Sem Hardware Real)

Detectado automaticamente em ambiente sem suporte a `RPi.GPIO`:

```bash
python main.py --mock

# Ou via Makefile
make run
```

### 4.5 Execução dos Testes Automatizados

```bash
pytest tests -v

# Via Makefile
make test
```

---

## 5. Interface de Linha de Comando (CLI)

```
--- CONTROLE DA CABINE 1 (FSE) ---
Comandos disponíveis:
  andar <0|1|2>           Desloca para o andar especificado
  motor <dir> <duty>      Acionamento direto (livre|subir|descer|freio, 0-100)
  status                  Exibe telemetria de sensores e atuadores
  parar                   Interrompe movimento e aciona freio
  ajuda                   Exibe lista de comandos
  sair                    Encerra a aplicação
----------------------------------
```

Exemplos de uso:
- `andar 1`
- `motor subir 40`
- `status`
- `parar`
- `sair`
