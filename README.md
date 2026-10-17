# Sistema de Controle de Grupo de Elevadores

Repositório do Trabalho 1 de **Fundamentos de Sistemas Embarcados (2026/2)**, da Faculdade do Gama (FGA/UnB). O projeto
evolui de um controlador local de uma cabine (Entrega 1), passando pela comunicação UART/MODBUS com a ESP32 que simula o
edifício (Entrega 2), até o **sistema distribuído de 3 cabines com Despacho por Destino** (Entrega Final), executado e
validado na Raspberry Pi da bancada.

## Entregas

| Entrega | Conteúdo | Documentação | Tag |
|:--|:--|:--|:-:|
| **Entrega 1** | Controle da Cabine 1 por GPIO: ponte H, PWM, encoder em quadratura por interrupção, cortina, sensor de andar, nivelamento e parada segura | [entrega1/README.md](./entrega1/README.md) | `v1.0` |
| **Entrega 2** | UART com a ESP32: protocolo simplificado, MODBUS modificado (matrícula de 6 dígitos, CRC-16) e funções `0x03`/`0x10` sobre os registradores do simulador | [entrega2/README.md](./entrega2/README.md) | `v2.0` |
| **Entrega Final** | Servidor Central + 3 Servidores Distribuídos por TCP/IP: calibração por interrupção, PID de posição, Cotas de Potência, Modo Degradado, Despacho por Destino, persistência e interface de terminal | [entrega_final/README.md](./entrega_final/README.md) | `v3.0` |

Vídeos de apresentação: [Entrega 1](https://youtu.be/1TL6987Bgqs?is=uhtylnDGjsFw261M) ·
[Entrega 2](https://youtu.be/jIa_jKNr2Eo?is=7PbL-yQusDOndkKr) · [Entrega Final](https://youtu.be/qjND2SU4JKI)

## Arquitetura da Entrega Final

```text
                    ┌──────────────── Servidor Central (1 processo) ────────────────┐
 BMP280 (I2C 0x76) ─┤ condição de contorno · Cotas de Potência · Despacho por Destino ├── /dev/serial0
                    │ persistência (estado + log de viagens) · painel de terminal    │   MODBUS RTU (ESP32)
                    └────────┬──────────────────┬──────────────────┬───────────────────┘
                     TCP/IP  │                  │                  │
                    ┌────────▼────┐      ┌──────▼──────┐    ┌──────▼──────┐
                    │  Cabine 1   │      │  Cabine 2   │    │  Cabine 3   │   Servidores Distribuídos
                    │ GPIO + PID  │      │ GPIO + PID  │    │ GPIO + PID  │   (malha de 50 ms)
                    └─────────────┘      └─────────────┘    └─────────────┘
```

Somente o Central acessa a UART e o I2C; cada Servidor Distribuído acessa apenas os 7 pinos GPIO da sua cabine.

## Estrutura do repositório

```text
.
├── entrega1/            # Módulo GPIO (Cabine 1): src/{cli,controller,hal,sensors}, tests/
├── entrega2/            # UART + MODBUS: src/{crc,uart_driver,parte1..3}, tests/
└── entrega_final/
    ├── config/          # cabine1..3.json (pinos BCM, MODBUS, TCP, PID) e central.json
    ├── elevador/
    │   ├── cabine/      # HAL GPIO, encoder, PID, movimento, calibração, Cota, runtime
    │   ├── central/     # serviço, Despacho, potência, BMP280, persistência, painel
    │   ├── modbus/      # CRC-16, UART, cliente 0x03/0x10, fila com prioridades
    │   ├── protocolo/   # mensagens TCP (JSON por linha) validadas
    │   ├── dominio/     # agenda de paradas e estimativa de tempos
    │   └── sim/         # bancada virtual (--mock) com o modelo medido do firmware
    ├── scripts/         # execução simulada completa e geração dos gráficos
    ├── docs/graficos/   # gráficos de sintonia (virtual) e da bancada real
    └── tests/           # 160 testes automatizados
```

## Requisitos

- Python 3.11 ou superior (validado com 3.12 no PC e 3.13 na Raspberry Pi)
- Hardware: Raspberry Pi com UART (`/dev/serial0`) e I2C habilitados, conectada à ESP32 do simulador
- Desenvolvimento: os modos `--mock` executam tudo sem a bancada

Cada entrega tem o seu `requirements.txt`; instale as dependências dentro da pasta da entrega que será executada.

## Instalação

```bash
cd entrega_final            # ou entrega1 / entrega2
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Execução

### Entrega Final — bancada virtual (sem hardware)

```bash
cd entrega_final
make run-all-mock           # bancada + Central + 3 cabines; Ctrl+C encerra tudo com segurança
```

### Entrega Final — Raspberry Pi

Cada serviço pode ser iniciado em qualquer ordem, num terminal próprio; as conexões se restabelecem sozinhas.

```bash
cd entrega_final
python -m elevador.central --config config/central.json
python -m elevador.cabine  --config config/cabine1.json    # idem cabine2.json e cabine3.json
```

Comandos do painel do Central: `ir <cabine> <andar>`, `cotas off|on`, `recalibrar <cabine>`, `diag`, `sair`.

### Entregas 1 e 2

```bash
cd entrega1 && python main.py --mock     # hardware: python main.py
cd entrega2 && python main.py --mock     # hardware: python main.py /dev/serial0
```

## Testes e qualidade

```bash
cd entrega_final && make test && make lint    # 160 testes + ruff
cd entrega1 && python -m pytest tests -v
cd entrega2 && python -m pytest -v && make lint
```

## Destaques da Entrega Final

- **GPIO por interrupção:** encoder em quadratura 4× nos canais A e B (contador `int32` com sinal), cortina com debounce
  e Sensor de Andar nas duas bordas; PWM de 1 kHz; sem busy-wait.
- **Calibração e reancoragem:** posição de cada andar pela média das bordas da bandeirola em varreduras de subida e de
  descida; reancoragem a cada passagem por bandeirola.
- **PID de posição (50 ms):** rampa de aceleração acima do atrito estático, anti-windup e parada preditiva; nivelamento
  dentro de ±5 mm medido na bancada (tolerância de ±10 mm).
- **Potência:** Cotas renovadas a cada 1 s com soma ≤ capacidade do barramento, duty limitado pelo modelo de corrente
  medido no firmware e Modo Degradado (pouso no próximo andar com a porta aberta).
- **Despacho por Destino:** atribuição definitiva pela fila do dispositivo `0x20`, custo por simulação da agenda,
  agrupamento de destinos, anti-inanição e lotação de 8 passageiros.
- **Robustez:** reconexão TCP automática, estado persistido e restaurado após reinício, log de viagens em
  `viagens.jsonl` e `SIGINT` deixando PWM em 0 % e os motores em freio.

## Validação na bancada

A Entrega Final foi executada na Raspberry Pi **rasp32** com o simulador real: calibração das 3 cabines, 132 chegadas
niveladas dentro de ±5 mm, 228 viagens reais concluídas sem perda, Modo Degradado, reconexão e encerramento seguro. Os
resultados, os gráficos e as limitações encontradas (como a ausência do BMP280 nessa bancada) estão na
[seção 8.1 do README da Entrega Final](./entrega_final/README.md#81-validação-na-bancada-real-rasp32-1516102026).

## Autores

| Nome | Usuário |
|:--|:--|
| Pedro Henrique | `pedrohpsantos` |
| Carlos | `Carlos-UCH` |

### Sobre o histórico de commits

O trabalho foi feito em dupla, de forma **presencial e na mesma máquina** nas Entregas 1 e 2; por isso quase todos os
commits saíram de uma única conta (`pedrohpsantos` / `Pedro Henrique`), embora o código seja dos dois. Seguindo a
orientação do professor, os dois integrantes constam como coautores: a partir do commit `d9ae363` todos os commits
levam `Co-authored-by: Carlos-UCH`. Os commits anteriores não puderam ser editados para incluir a coautoria, porque
a branch `main` do GitLab é protegida contra reescrita de histórico; ainda assim, **todo o trabalho desses commits
também é de autoria do Carlos**.

Disciplina: **Fundamentos de Sistemas Embarcados** · Período: **2026/2** ·
Instituição: **Faculdade do Gama — Universidade de Brasília (FGA/UnB)**
