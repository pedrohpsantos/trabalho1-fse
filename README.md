# Sistema de Controle e Comunicação de Elevadores

Repositório das entregas do trabalho de **Fundamentos de Sistemas Embarcados (2026/2)**, da Faculdade do Gama (FGA/UnB). O projeto evolui de um controlador local de cabine, executado em Raspberry Pi, para uma camada de comunicação UART com uma ESP32 que simula o edifício de elevadores.

## Visão geral

O repositório está organizado em duas entregas independentes e complementares:

- **[Entrega 1](./entrega1/README.md)** — controle da Cabine 1 por GPIO, com acionamento do motor, PWM, encoder em quadratura, sensores, nivelamento, limites de segurança e modo simulado.
- **[Entrega 2](./entrega2/README.md)** — comunicação serial UART entre Raspberry Pi e ESP32, com protocolo simplificado, wrapper MODBUS modificado, CRC-16 e acesso aos registradores do simulador de elevadores.

- **[Entrega Final](./entrega_final/README.md)** — sistema distribuído completo (Servidor Central + 3 Servidores Distribuídos), calibração, PID, Cotas de potência, Despacho por Destino e bancada virtual `--mock`.

As documentações específicas apresentam o detalhamento de hardware, protocolos, arquitetura, comandos da CLI, exemplos de tráfego e testes de cada etapa.

## Estrutura do repositório

```text
.
├── entrega1/
│   ├── main.py
│   ├── diagnostico.py
│   ├── src/
│   │   ├── cli/          # Interface de linha de comando
│   │   ├── controller/   # Máquina de estados e movimento
│   │   ├── hal/          # Abstração de hardware, mock e Raspberry Pi
│   │   └── sensors/      # Tratamento dos sensores
│   ├── tests/
│   └── README.md
└── entrega2/
    ├── main.py
    ├── src/
    │   ├── config.py
    │   ├── crc.py
    │   ├── uart_driver.py
    │   ├── parte1_simplificado.py
    │   ├── parte2_modbus_p2.py
    │   └── parte3_simulador.py
    ├── tests/
    └── README.md
```

## Requisitos gerais

- Python 3
- Para execução em hardware: Raspberry Pi e os periféricos descritos na documentação da entrega correspondente
- Para a Entrega 2 em hardware: Raspberry Pi conectada à ESP32 por UART, com GND comum
- Para desenvolvimento local: os modos `--mock` permitem executar os projetos sem a bancada

Cada entrega possui seu próprio `requirements.txt`; as dependências devem ser instaladas a partir da pasta do projeto que será executado.

## Instalação

É recomendado utilizar um ambiente virtual separado para cada entrega:

```bash
cd entrega1
python -m venv venv
```

No Windows:

```powershell
venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

No Linux ou Raspberry Pi:

```bash
source venv/bin/activate
python -m pip install -r requirements.txt
```

Para instalar as dependências da segunda entrega, repita o procedimento em `entrega2`.

## Execução rápida em modo simulado

### Entrega 1 — controle da cabine

```bash
cd entrega1
python main.py --mock
```

Alternativamente:

```bash
make run
```

### Entrega 2 — comunicação UART simulada

```bash
cd entrega2
python main.py --mock
```

Alternativamente:

```bash
make run
```

O modo simulado é indicado para desenvolvimento, testes e demonstrações sem acesso à Raspberry Pi, GPIO ou ESP32.

## Execução em hardware

### Entrega 1

Na Raspberry Pi, com a pinagem da bancada configurada:

```bash
cd entrega1
python main.py
```

Para a configuração alternativa do widget ThingsBoard:

```bash
python main.py --widget
```

Antes da operação, o estado elétrico dos pinos pode ser verificado com:

```bash
python diagnostico.py
```

### Entrega 2

Com a Raspberry Pi conectada à ESP32 pela UART:

```bash
cd entrega2
python main.py /dev/serial0
```

A porta, o timeout e a matrícula podem ser configurados conforme descrito em [Configuração da Entrega 2](./entrega2/README.md).

## Testes e qualidade

Os testes devem ser executados dentro da pasta de cada entrega:

```bash
cd entrega1
python -m pytest tests -v
```

```bash
cd entrega2
python -m pytest -v
```

A Entrega 2 também disponibiliza verificações com Ruff:

```bash
cd entrega2
make lint
make format
```

Os alvos disponíveis em cada `Makefile` incluem instalação, execução simulada, execução em hardware, testes e limpeza de caches.

## Funcionalidades principais

### Controle local da cabine

- Controle de tração por ponte H nos estados livre, subir, descer e freio
- PWM de 1 kHz com rampa de velocidade
- Leitura de encoder em quadratura 4x
- Debounce da cortina de luz
- Detecção de bandeirola e cálculo do centro do andar
- Nivelamento com tolerância de ±5 mm
- Fim de curso entre 0 e 6000 mm
- Parada segura em `SIGINT`
- Driver mock para testes sem hardware

### Comunicação com o simulador

- Protocolo simplificado com seis comandos de solicitação e envio
- Wrapper MODBUS modificado com funções `0x23` e `0x16`
- CRC-16 e validação de quadros
- Primitivas MODBUS `0x03` e `0x10`
- Leitura de estado das três cabines e do controlador do prédio
- Escrita das condições ambientais de temperatura e pressão
- Retentativas para timeout e CRC
- Tratamento das exceções MODBUS `0x01`, `0x02` e `0x03`
- Monitoramento contínuo e simulador UART offline

## Documentação detalhada

- [README da Entrega 1 — Controle GPIO](./entrega1/README.md)
- [README da Entrega 2 — UART e MODBUS](./entrega2/README.md)

Os READMEs das entregas devem ser consultados antes da ligação do hardware, pois contêm os mapas de pinos, parâmetros de comunicação, formatos de pacotes e procedimentos específicos de diagnóstico.

## Contexto acadêmico

Disciplina: **Fundamentos de Sistemas Embarcados**  
Período: **2026/2**  
Instituição: **Faculdade do Gama — Universidade de Brasília (FGA/UnB)**
