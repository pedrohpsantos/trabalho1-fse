# Comunicação UART (Protocolo Simplificado + MODBUS Modificado)

Trabalho 1 — **Entrega 2**  
Disciplina de **Fundamentos de Sistemas Embarcados (2026/2)**  
Faculdade do Gama — Universidade de Brasília (FGA-UnB)

---

## 1. Objetivos

Implementar, na **Raspberry Pi**, a camada de comunicação serial UART com a **ESP32** (simulador do edifício de elevadores) contemplando:

1. **Parte 1 — Protocolo Simplificado**: 6 comandos enxutos (3 de solicitação e 3 de envio), sem endereçamento e sem CRC, com dados em Little-Endian e matrícula de 6 dígitos brutos.
2. **Parte 2 — Wrapper MODBUS Modificado**: 6 comandos adaptados sobre o padrão MODBUS RTU (`0x23` e `0x16`), incluindo cálculo e validação do CRC-16 e matrícula de 6 dígitos antes do CRC.
3. **Parte 3 — Registradores do Simulador de Elevadores**: Primitivas genéricas `0x03` (*Read Holding Registers*) e `0x10` (*Write Multiple Registers*), manipulação das 3 cabines (`0x11`, `0x12`, `0x13`) e do Controlador do Prédio (`0x20`), política de 3 retentativas para timeout/CRC, tratamento de exceções MODBUS (`0x01`, `0x02`, `0x03`), conversão de posição com sinal (`int16`) e módulo desacoplado importável pela Entrega Final.

---

## 2. Hardware e Conexão Física

| Componente | Função |
|:---|:---|
| **Raspberry Pi 4** | Controlador mestre (executa a aplicação e a CLI) |
| **ESP32** | Simulador em tempo real do edifício e cabines de elevador |
| **UART Serial** | Barramento de comunicação serial assíncrona bidirecional |

### Diagrama de Conexão UART

| Pino Raspberry Pi | Sinal RPi | Pino ESP32 | Sinal ESP32 |
|:---:|:---:|:---:|:---:|
| Pino 8 (GPIO 14) | **UART_TXD** | Pino RX (GPIO 16) | **RX** |
| Pino 10 (GPIO 15) | **UART_RXD** | Pino TX (GPIO 17) | **TX** |
| Pino 6, 9, 14, 20 ou 25 | **GND** | Pino GND | **GND** |

> ⚠️ **Atenção:** As duas placas devem obrigatoriamente compartilhar a mesma referência de terra (**GND comum**).

---

## 3. Configuração da UART

| Parâmetro | Valor Configurado |
|:---|:---|
| **Baudrate** | `115200` bps |
| **Bits de dados** | `8` bits |
| **Paridade** | Nenhuma (`None`) |
| **Bits de parada** | `1` stop bit |
| **Controle de fluxo** | Nenhum |
| **Porta padrão** | `/dev/serial0` (ou `/dev/ttyS0`) |
| **Timeout de recepção** | `0.5` s (configurável de 0.2 a 0.5 s) |
| **Formato de exibição** | Bytes sempre impressos em hexadecimal (`[TX]`, `[RX]`) |

---

## 4. Configuração da Matrícula

A matrícula do aluno identifica a autoria de cada pacote no ThingsBoard e nas transações:

- **Regra obrigatória:** São utilizados os **6 últimos dígitos** da matrícula;
- **Codificação:** Cada dígito é transmitido como **byte cru de valor numérico inteiro** de `0x00` a `0x09` (ex: dígito `6` é enviado como byte `0x06`, **nunca** como o caractere ASCII `'6'` / `0x36`);
- **Localização:** A matrícula está centralizada no arquivo `src/config.py` através da variável `MATRICULA_PADRAO`:

```python
# src/config.py
MATRICULA_PADRAO: List[int] = [6, 5, 4, 3, 2, 1]  # Substitua pelos 6 últimos dígitos reais
```

Também é possível sobrescrever via linha de comando ao iniciar a CLI:
```bash
python main.py --matricula 654321
```

A função `validar_e_obter_matricula()` valida estritamente o tamanho de 6 elementos e os valores inteiros permitidos.

---

## 5. Estrutura do Projeto

```text
entrega-2-fse/
├── Makefile                    # Alvos de automação (run, rpi, test, lint, format)
├── requirements.txt            # Dependências Python (pyserial, pytest, ruff)
├── README.md                   # Documentação do projeto
├── main.py                     # CLI unificada com menus e monitoramento contínuo
├── src/
│   ├── __init__.py             # Exportação dos componentes da biblioteca
│   ├── config.py               # Configurações de matrícula, UART e exceções
│   ├── crc.py                  # Cálculo e verificação do CRC-16 (algoritmo de referência)
│   ├── uart_driver.py          # Driver serial (HardwareUartDriver + FakeUartDriver)
│   ├── parte1_simplificado.py  # 6 comandos da Parte 1 (sem CRC, Little-Endian)
│   ├── parte2_modbus_p2.py     # 6 comandos da Parte 2 (wrapper 0x23/0x16 + CRC-16)
│   └── parte3_simulador.py     # Cliente MODBUS 0x03 e 0x10 importável e regras de negócio
└── tests/
    ├── __init__.py
    ├── test_config.py          # Validação estrita da matrícula
    ├── test_crc.py             # Validação dos vetores oficiais do enunciado
    ├── test_parte1.py          # Testes dos comandos da Parte 1
    ├── test_parte2.py          # Testes do wrapper e CRC da Parte 2
    ├── test_parte3.py          # Testes de primitivas 0x03/0x10, endianness e retentativas
    └── test_edge_cases.py      # Testes de exceções, cabeçalhos inválidos e importabilidade
```

---

## 6. Instalação e Execução

### 6.1 Instalação das Dependências

Recomenda-se criar um ambiente virtual Python:

```bash
python -m venv venv
source venv/bin/activate  # No Linux/RPi
# ou: venv\Scripts\activate no Windows

pip install -r requirements.txt
```

### 6.2 Execução na Raspberry Pi (Hardware Real)

Conecte a Raspberry Pi à ESP32 pela UART e execute:

```bash
# Execução na porta padrão (/dev/serial0)
python main.py /dev/serial0

# Ou via Makefile
make rpi
```

### 6.3 Execução em Modo Simulado (Sem Hardware / Mock Offline)

Para desenvolvimento, validação e gravação fora da bancada, o sistema possui um `FakeUartDriver` integrado que simula todas as respostas da ESP32:

```bash
# Execução com simulação offline
python main.py --mock

# Ou via Makefile
make run
```

---

## 7. Exemplos de Comandos e Tráfego Hexadecimal

### 7.1 Parte 1 — Protocolo Simplificado

#### Solicita Inteiro (`0xA1`)
- **TX (7 B):** `A1 06 05 04 03 02 01`
- **RX (4 B):** `2A 00 00 00` (valor `42` em int32 LE)

#### Envia Inteiro 3245 (`0xB1`)
- **TX (11 B):** `B1 AD 0C 00 00 06 05 04 03 02 01` (3245 em int32 LE: `0xAD 0x0C 0x00 0x00`)
- **RX (4 B):** `AD 0C 00 00` (resposta calculada: $3245 \times 1 = 3245$)

#### Envia String `"Teste"` (`0xB3`)
- **TX (13 B):** `B3 05 54 65 73 74 65 06 05 04 03 02 01`
- **RX (23 B):** `17 52 65 73 70 6F 73 74 61 20 64 61 20 55 41 52 54 3A 20 54 65 73 74 65` (`"Resposta da UART: Teste"`)

---

### 7.2 Parte 2 — Wrapper MODBUS Didático

#### Solicita Float (`0x23`, Sub `0xA2`)
- **TX (11 B):** `01 23 A2 06 05 04 03 02 01 [CRC_LO] [CRC_HI]`
- **RX (8 B):** `01 23 [float LE: 4 B] [CRC_LO] [CRC_HI]`

#### Envia Inteiro 3245 (`0x16`, Sub `0xB1`)
- **TX (15 B):** `01 16 B1 AD 0C 00 00 06 05 04 03 02 01 29 47`
- **RX (8 B):** `01 16 AD 0C 00 00 5D 83`

---

### 7.3 Parte 3 — Registradores do Simulador

#### Lê Estado da Cabine 1 (`0x11`, offsets 0 a 8 via `0x03`)
- **TX (14 B):** `11 03 00 00 09 00 06 05 04 03 02 01 0E E4`
  - `reg = 0` (LE: `00 00`), `qtd = 9` (LE: `09 00`), matrícula `06 05 04 03 02 01`, CRC `0E E4`.
- **RX (23 B):** `11 03 12 [andar BE: 2] [nivelado BE: 2] ... [posicao_mm BE: 2] [falha BE: 2] [CRC_LO] [CRC_HI]`

#### Escreve Condição de Contorno no Prédio (`0x20`, offsets 5 e 6: 25.3 °C e 1013 hPa via `0x10`)
- **TX (19 B):** `20 10 05 00 02 00 04 FD 00 F5 03 06 05 04 03 02 01 56 C3`
  - `reg = 5` (LE: `05 00`), `qtd = 2` (LE: `02 00`), `byte_count = 4` (`04`), `253` (LE: `FD 00`), `1013` (LE: `F5 03`), matrícula `06 05 04 03 02 01`, CRC `56 C3`.
- **RX (8 B):** `20 10 00 05 00 02 57 63`
  - Eco de `reg = 5` (BE: `00 05`) e `qtd = 2` (BE: `00 02`), CRC `57 63`.

---

## 8. Tratamento de Erros e Exceções

1. **Timeout**: Quando o dispositivo não responde no prazo configurado (`0.5 s`), o driver registra `<TIMEOUT>` e lança `TimeoutError`.
2. **Falha de CRC**: Respostas com CRC incorreto são descartadas imediatamente. Na Parte 3, realizam-se até **3 tentativas** com intervalo de 50 ms.
3. **Exceções MODBUS**:
   - `0x01` (Função inválida);
   - `0x02` (Endereço inválido / registrador somente-leitura);
   - `0x03` (Valor inválido).
   - Quando o dispositivo retorna o bit 7 ativo na função (`resp[1] & 0x80`), a exceção é decodificada e **não é repetida**, lançando `RuntimeError` imediatamente.

---

## 9. Como Importar o Módulo da Parte 3 na Entrega Final

O módulo `SimuladorModbusClient` é completamente desacoplado da CLI e pode ser utilizado diretamente pelo **Servidor Central** e pelos **Servidores Distribuídos**:

```python
from src.uart_driver import criar_uart_driver
from src.parte3_simulador import SimuladorModbusClient

# Inicialização pelo Servidor Central ou Distribuído
driver = criar_uart_driver(porta="/dev/serial0", timeout=0.5)
cliente_modbus = SimuladorModbusClient(driver, matricula=[6, 5, 4, 3, 2, 1])

# 1. Leitura do estado de uma cabine (ex: Cabine 1)
estado = cliente_modbus.le_estado_cabine(cabine=1)
print(f"Andar: {estado['andar_atual']}, Posição: {estado['posicao_mm']} mm, Porta: {estado['porta_estado']}")

# 2. Envio da condição de contorno (temperatura BMP280 e pressão)
cliente_modbus.escreve_condicao_contorno(temp_c=24.8, press_hpa=1012)

# 3. Leitura e despacho de chamadas da fila
chamada = cliente_modbus.le_chamada_da_fila()
if chamada["pendentes"] > 0:
    print(f"Atendendo chamada #{chamada['id']} do andar {chamada['origem']} para o andar {chamada['destino']}")
    cliente_modbus.atribui_chamada(chamada_id=chamada["id"], cabine=1)
    cliente_modbus.remove_chamada_da_fila()
```

---

## 10. Execução dos Testes Automatizados

A suíte de testes cobre 100% dos requisitos (43 testes unitários e de integração mockada):

```bash
# Executa todos os testes
pytest -v

# Ou via Makefile
make test
```

Para verificação estática de código e formatação:
```bash
make lint
make format
```

---

## 11. Evidências do ThingsBoard

> **Espaço reservado para inserção da captura de tela da bancada:**
>
> Insira aqui o print do widget UART do ThingsBoard com os **6 dígitos da sua matrícula** destacados em amarelo no log de mensagens recebidas:
>
> ![Dashboard UART com Matrícula](./imagens/Dashboard_UART_matricula.png)

---

## 12. Roteiro Sugerido para o Vídeo de Apresentação (Até 8 Minutos)

1. **Abertura (0:00 - 0:45)**:
   - Câmera aberta exibindo os integrantes da equipe;
   - Apresentação nominal dos participantes e leitura da matrícula;
   - Breve introdução dos objetivos da Entrega 2.
2. **Parte 1 — Protocolo Simplificado (0:45 - 2:00)**:
   - Executar os comandos 1 a 6 da Parte 1 no menu interativo;
   - Destacar os bytes `[TX]` e `[RX]` no terminal em hexadecimal e a decodificação dos valores (LE);
   - Mostrar a reação correspondente no widget UART do ThingsBoard.
3. **Parte 2 — Wrapper MODBUS (2:00 - 3:30)**:
   - Executar os comandos 1 a 6 da Parte 2;
   - Mostrar o encapsulamento com `0x23` e `0x16`, a inclusão dos 6 dígitos e os 2 bytes de CRC-16 no final;
   - Mostrar a validação de resposta com verificação de CRC.
4. **Parte 3 — Simulador de Elevadores (3:30 - 6:30)**:
   - **Watchdog e Derating**: Mostrar `le_estado_predio()` com `watchdog_ambiente = 1` e barramento em 3000 mA. Enviar `escreve_condicao_contorno()` e mostrar o watchdog voltando a `0` e o barramento subindo para 12000 mA;
   - **Comando de Portas**: Acionar `comanda_porta(1, 1)` (abrir) e `comanda_porta(1, 2)` (fechar) e acompanhar a transição de estado via `le_estado_cabine(1)`;
   - **Fila de Chamadas**: Criar uma chamada no quiosque web do ThingsBoard, ler com `le_chamada_da_fila()`, atribuir com `atribui_chamada()` e retirá-la com `remove_chamada_da_fila()`;
   - **Exceções**: Disparar o teste de escrita em registrador Read-Only e demonstrar a captura da exceção `0x02`.
5. **Monitoramento Contínuo e Conclusão (6:30 - 7:30)**:
   - Iniciar o modo de leitura contínua (1 Hz) e demonstrar a telemetria ao vivo;
   - Encerrar com `Ctrl+C` exibindo o tratamento de sinal limpo;
   - Encerramento pelos integrantes.