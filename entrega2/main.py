"""main.py - Interface de Linha de Comando (CLI) da Entrega 2.

Fundamentos de Sistemas Embarcados (2026/2) - FGA/UnB
Suporta:
- Menus independentes para Parte 1, Parte 2 e Parte 3;
- Operações de cabine (0x11..0x13) e prédio (0x20);
- Primitivas diretas 0x03 e 0x10;
- Monitoramento contínuo a 1 Hz;
- Modo hardware físico (/dev/serial0) ou simulado (--mock);
- Encerramento limpo em Ctrl+C (SIGINT).
"""

import argparse
import sys
import time

from src.config import (
    UART_PORTA_PADRAO,
    UART_TIMEOUT_PADRAO,
    validar_e_obter_matricula,
)
from src.parte1_simplificado import ProtocoloSimplificado
from src.parte2_modbus_p2 import ProtocoloModbusP2
from src.parte3_simulador import SimuladorModbusClient
from src.uart_driver import criar_uart_driver


def formatar_painel_cabine(estado: dict) -> None:
    """Exibe o estado de uma cabine de forma visual e explicada."""
    cab = estado["cabine"]
    addr = 0x10 + cab
    andar = estado["andar_atual"]
    nome_andar = "Térreo" if andar == 0 else f"{andar}º Andar"
    nivelado_str = "SIM (no nível exato do pavimento) [OK]" if estado["nivelado"] else "NÃO (em trânsito ou desnivelado)"
    porta = estado["porta_estado"]
    cmd_porta = estado["porta_comando"]
    cmd_desc = {0: "Nenhum", 1: "Abrir", 2: "Fechar"}.get(cmd_porta, str(cmd_porta))
    corrente = estado["corrente_ma"]
    carga = estado["carga_kg"]
    passag = estado["passageiros"]
    pos_mm = estado["posicao_mm"]
    falha = estado["falha"]
    falha_desc = "Normal / Sem alarmes" if falha == 0 else f"Código {falha} (0x{falha:02X})"

    print("\n" + "=" * 65)
    print(f"       ESTADO DA CABINE {cab} (Endereço MODBUS: 0x{addr:02X})")
    print("=" * 65)
    print(f"  • Andar Atual:         {andar} ({nome_andar})")
    print(f"  • Posição Vertical:    {pos_mm:+d} mm (offset 7, int16 com sinal)")
    print(f"  • Status Nivelamento:  {nivelado_str}")
    print(f"  • Estado da Porta:     {porta} (Comando ativo: {cmd_desc})")
    print(f"  • Ocupação:            {passag} passageiro(s) | Carga: {carga} kg")
    print(f"  • Corrente do Motor:   {corrente} mA")
    print(f"  • Status de Operação:  {falha_desc}")
    print("=" * 65)


def formatar_painel_predio(predio: dict) -> None:
    """Exibe o estado do prédio de forma detalhada e explicada."""
    fila = predio["chamadas_na_fila"]
    wdt = predio["watchdog_ambiente"]
    barr = predio["barramento_max_ma"]
    cenario = predio["cenario_ativo"]
    geradas = predio["cenario_geradas"]
    atendidas = predio["cenario_atendidas"]
    espera = predio["espera_media_s"]
    viagem = predio["viagem_media_s"]
    temp = predio.get("ambiente_temp_c", 0.0)
    press = predio.get("ambiente_press_hpa", 0)

    wdt_status = "[EXPIRADO - Potência reduzida!]" if wdt == 1 else "[NORMAL / ALIMENTADO]"
    barr_status = f"{barr} mA" + (" (Reduzido por proteção térmica)" if wdt == 1 else " (Nominal)")
    espera_str = "Sem chamadas finalizadas ainda" if espera >= 6553.0 else f"{espera:.1f} s"

    print("\n" + "=" * 65)
    print("       ESTADO DO CONTROLADOR DO PRÉDIO (Endereço MODBUS: 0x20)")
    print("=" * 65)
    print(f"  • Fila de Chamadas:    {fila} chamada(s) aguardando atendimento")
    if fila > 0:
        print(f"    └─ Próxima Chamada:  ID #{predio['chamada_id']} | Andar {predio['chamada_origem']} ➔ Andar {predio['chamada_destino']}")
    else:
        print("    └─ Nenhuma chamada pendente no momento")
    print(f"  • Barramento Energia:  {barr_status}")
    print(f"  • Watchdog Ambiente:   {wdt} {wdt_status}")
    if temp > 0 or press > 0:
        print(f"  • Condição Contorno:   {temp:.1f} °C | {press} hPa")
    print(f"  • Cenário de Tráfego:  Cenário {cenario} ({geradas} geradas | {atendidas} atendidas)")
    print(f"  • Tempos Médios:       Espera: {espera_str} | Viagem: {viagem:.1f} s")
    print("=" * 65)
    if wdt == 1:
        print("  [! AVISO]: O Watchdog expirou! O barramento foi limitado a 3000 mA.")
        print("             Envie a Opção 4 (Condição de Contorno) para normalizar (12000 mA).")


def menu_parte1(p1: ProtocoloSimplificado) -> None:
    """Menu interativo dos 6 comandos da Parte 1 (Protocolo Simplificado)."""
    ult_digito = p1.matricula_bytes[-1]
    mat_str = "".join(str(b) for b in p1.matricula_bytes)

    while True:
        print("\n" + "=" * 55)
        print("   PARTE 1 — PROTOCOLO SIMPLIFICADO (SEM CRC)")
        print(f"   Matrícula ativa: {mat_str} | Último dígito: {ult_digito}")
        print("=" * 55)
        print("1. Solicita Inteiro (0xA1)")
        print("2. Solicita Float (0xA2)")
        print("3. Solicita String (0xA3)")
        print("4. Envia Inteiro (0xB1)")
        print("5. Envia Float (0xB2)")
        print("6. Envia String (0xB3)")
        print("0. Voltar ao menu principal")
        opcao = input("\nEscolha uma opção: ").strip()

        try:
            if opcao == "1":
                val = p1.solicita_inteiro()
                print("\n" + "-" * 55)
                print("[RESPOSTA RECEBIDA - PARTE 1 / Comando 0xA1]")
                print(f"  • Valor Inteiro:  {val} (0x{val & 0xFFFFFFFF:08X} LE)")
                print("  • Significado:    Constante numérica retornada pelo simulador")
                print("  • Status:         Sucesso (4 bytes inteiros decodificados)")
                print("-" * 55)
            elif opcao == "2":
                val = p1.solicita_float()
                print("\n" + "-" * 55)
                print("[RESPOSTA RECEBIDA - PARTE 1 / Comando 0xA2]")
                print(f"  • Valor Float:    {val:.6f}")
                print("  • Significado:    Constante float (Pi / π) retornada pelo simulador")
                print("  • Status:         Sucesso (4 bytes IEEE-754 LE decodificados)")
                print("-" * 55)
            elif opcao == "3":
                val = p1.solicita_string()
                print("\n" + "-" * 55)
                print("[RESPOSTA RECEBIDA - PARTE 1 / Comando 0xA3]")
                print(f"  • Texto Recebido: \"{val}\"")
                print(f"  • Comprimento:    {len(val)} caracteres")
                print("  • Status:         Sucesso (string UTF-8 recebida)")
                print("-" * 55)
            elif opcao == "4":
                entrada = input("Digite o valor inteiro a enviar (ex: 3245): ").strip()
                v_in = int(entrada)
                val = p1.envia_inteiro(v_in)
                print("\n" + "-" * 55)
                print("[RESPOSTA RECEBIDA - PARTE 1 / Comando 0xB1]")
                print(f"  • Valor Enviado:    {v_in}")
                print(f"  • Multiplicador:    {ult_digito} (último dígito da matrícula {mat_str})")
                print(f"  • Valor Retornado:  {val}")
                print(f"  • Cálculo do Eco:   {v_in} × {ult_digito} = {val}")
                print("  • Status:           Sucesso (eco processado corretamente pela ESP32)")
                print("-" * 55)
            elif opcao == "5":
                entrada = input("Digite o valor float a enviar (ex: 12.75): ").strip()
                v_in = float(entrada)
                val = p1.envia_float(v_in)
                print("\n" + "-" * 55)
                print("[RESPOSTA RECEBIDA - PARTE 1 / Comando 0xB2]")
                print(f"  • Valor Enviado:    {v_in:.4f}")
                print(f"  • Multiplicador:    {ult_digito} (último dígito da matrícula {mat_str})")
                print(f"  • Valor Retornado:  {val:.4f}")
                print(f"  • Cálculo do Eco:   {v_in:.4f} × {ult_digito} = {val:.4f}")
                print("  • Status:           Sucesso (eco float processado corretamente)")
                print("-" * 55)
            elif opcao == "6":
                entrada = input("Digite o texto a enviar: ").strip()
                val = p1.envia_string(entrada)
                print("\n" + "-" * 55)
                print("[RESPOSTA RECEBIDA - PARTE 1 / Comando 0xB3]")
                print(f"  • Texto Enviado:    \"{entrada}\"")
                print(f"  • Texto Retornado:  \"{val}\"")
                print("  • Formato do Eco:   Prefixo 'Resposta da UART: ' + texto enviado")
                print("  • Status:           Sucesso (eco de string processado com sucesso)")
                print("-" * 55)
            elif opcao == "0":
                break
            else:
                print("[AVISO] Opção inválida.")
        except KeyboardInterrupt:
            print("\nOperação cancelada pelo usuário.")
            break
        except Exception as e:  # noqa: BLE001
            print(f"[FALHA NA OPERAÇÃO] {e}")


def menu_parte2(p2: ProtocoloModbusP2) -> None:
    """Menu interativo dos 6 comandos da Parte 2 (Wrapper MODBUS Didático)."""
    ult_digito = p2.matricula_bytes[-1]
    mat_str = "".join(str(b) for b in p2.matricula_bytes)

    while True:
        print("\n" + "=" * 55)
        print("   PARTE 2 — MODBUS MODIFICADO (WRAPPER 0x23 / 0x16)")
        print(f"   Matrícula ativa: {mat_str} | Último dígito: {ult_digito}")
        print("=" * 55)
        print("1. Solicita Inteiro (0x23, Sub 0xA1)")
        print("2. Solicita Float (0x23, Sub 0xA2)")
        print("3. Solicita String (0x23, Sub 0xA3)")
        print("4. Envia Inteiro (0x16, Sub 0xB1)")
        print("5. Envia Float (0x16, Sub 0xB2)")
        print("6. Envia String (0x16, Sub 0xB3)")
        print("0. Voltar ao menu principal")
        opcao = input("\nEscolha uma opção: ").strip()

        try:
            if opcao == "1":
                val = p2.solicita_inteiro()
                print("\n" + "-" * 55)
                print("[RESPOSTA MODBUS - PARTE 2 / Função 0x23, Subcódigo 0xA1]")
                print(f"  • Valor Inteiro:  {val} (0x{val & 0xFFFFFFFF:08X} LE)")
                print("  • Integridade:    CRC-16 validado com sucesso")
                print("  • Significado:    Inteiro retornado pelo wrapper MODBUS")
                print("-" * 55)
            elif opcao == "2":
                val = p2.solicita_float()
                print("\n" + "-" * 55)
                print("[RESPOSTA MODBUS - PARTE 2 / Função 0x23, Subcódigo 0xA2]")
                print(f"  • Valor Float:    {val:.6f}")
                print("  • Integridade:    CRC-16 validado com sucesso")
                print("  • Significado:    Float (Pi / π) retornado pelo wrapper MODBUS")
                print("-" * 55)
            elif opcao == "3":
                val = p2.solicita_string()
                print("\n" + "-" * 55)
                print("[RESPOSTA MODBUS - PARTE 2 / Função 0x23, Subcódigo 0xA3]")
                print(f"  • Texto Recebido: \"{val}\"")
                print(f"  • Tamanho:        {len(val)} caracteres")
                print("  • Integridade:    CRC-16 validado com sucesso")
                print("-" * 55)
            elif opcao == "4":
                entrada = input("Digite o valor inteiro a enviar (ex: 3245): ").strip()
                v_in = int(entrada)
                val = p2.envia_inteiro(v_in)
                print("\n" + "-" * 55)
                print("[RESPOSTA MODBUS - PARTE 2 / Função 0x16, Subcódigo 0xB1]")
                print(f"  • Valor Enviado:    {v_in}")
                print(f"  • Multiplicador:    {ult_digito} (último dígito da matrícula {mat_str})")
                print(f"  • Valor Retornado:  {val}")
                print(f"  • Cálculo do Eco:   {v_in} × {ult_digito} = {val}")
                print("  • Integridade:      CRC-16 validado com sucesso")
                print("-" * 55)
            elif opcao == "5":
                entrada = input("Digite o valor float a enviar (ex: 45.8): ").strip()
                v_in = float(entrada)
                val = p2.envia_float(v_in)
                print("\n" + "-" * 55)
                print("[RESPOSTA MODBUS - PARTE 2 / Função 0x16, Subcódigo 0xB2]")
                print(f"  • Valor Enviado:    {v_in:.4f}")
                print(f"  • Multiplicador:    {ult_digito} (último dígito da matrícula {mat_str})")
                print(f"  • Valor Retornado:  {val:.4f}")
                print(f"  • Cálculo do Eco:   {v_in:.4f} × {ult_digito} = {val:.4f}")
                print("  • Integridade:      CRC-16 validado com sucesso")
                print("-" * 55)
            elif opcao == "6":
                entrada = input("Digite o texto a enviar: ").strip()
                val = p2.envia_string(entrada)
                print("\n" + "-" * 55)
                print("[RESPOSTA MODBUS - PARTE 2 / Função 0x16, Subcódigo 0xB3]")
                print(f"  • Texto Enviado:    \"{entrada}\"")
                print(f"  • Texto Retornado:  \"{val}\"")
                print("  • Integridade:      CRC-16 validado com sucesso")
                print("-" * 55)
            elif opcao == "0":
                break
            else:
                print("[AVISO] Opção inválida.")
        except KeyboardInterrupt:
            print("\nOperação cancelada pelo usuário.")
            break
        except Exception as e:  # noqa: BLE001
            print(f"[FALHA NA OPERAÇÃO] {e}")


def monitoramento_continuo(p3: SimuladorModbusClient) -> None:
    """Modo de leitura contínua a cada 1 segundo (1 Hz)."""
    print("\n" + "=" * 70)
    print("   INICIANDO MONITORAMENTO CONTÍNUO EM TEMPO REAL (1 Hz)")
    print("   Pressione Ctrl+C a qualquer momento para interromper...")
    print("=" * 70)
    try:
        while True:
            c1 = p3.le_estado_cabine(1)
            pr = p3.le_estado_predio()

            niv_ico = "OK" if c1["nivelado"] else "MOV"
            wdt_ico = "OK" if pr["watchdog_ambiente"] == 0 else "EXPIRADO"
            barr_ma = pr["barramento_max_ma"]
            fila = pr["chamadas_na_fila"]
            pos = c1["posicao_mm"]
            andar = c1["andar_atual"]
            porta = c1["porta_estado"]
            passag = c1["passageiros"]

            print(
                f"[CABINE 1] Andar: {andar} | Pos: {pos:>5} mm [{niv_ico}] | "
                f"Porta: {porta:<8} | Passag: {passag} || "
                f"[PRÉDIO] Barramento: {barr_ma:>5} mA | WDT: {wdt_ico:<8} | Fila: {fila}"
            )
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n\n[MONITORAMENTO INTERROMPIDO PELO USUÁRIO]")


def menu_parte3(p3: SimuladorModbusClient) -> None:
    """Menu interativo das operações do simulador (Parte 3)."""
    while True:
        print("\n" + "=" * 55)
        print("   PARTE 3 — SIMULADOR DE ELEVADORES (0x03 E 0x10)")
        print("=" * 55)
        print("1. Ler Estado da Cabine (1, 2 ou 3)")
        print("2. Comandar Porta de Cabine (1=Abrir, 2=Fechar)")
        print("3. Ler Estado do Controlador do Prédio (0x20)")
        print("4. Escrever Condição de Contorno (Temperatura e Pressão)")
        print("5. Ler Próxima Chamada da Fila de Destino")
        print("6. Atribuir Chamada a uma Cabine")
        print("7. Remover Chamada da Fila (Pop)")
        print("8. Primitiva 0x03 Genérica (Read Holding Registers)")
        print("9. Primitiva 0x10 Genérica (Write Multiple Registers)")
        print("10. Teste de Exceção MODBUS (Escrita em Registrador Read-Only)")
        print("11. Leitura Contínua em Tempo Real (1 Hz)")
        print("0. Voltar ao menu principal")
        opcao = input("\nEscolha uma opção: ").strip()

        try:
            if opcao == "1":
                cab = int(input("Informe o número da cabine (1, 2 ou 3): ").strip())
                estado = p3.le_estado_cabine(cab)
                formatar_painel_cabine(estado)
            elif opcao == "2":
                cab = int(input("Informe a cabine (1, 2 ou 3): ").strip())
                cmd = int(input("Comando (1=Abrir, 2=Fechar): ").strip())
                p3.comanda_porta(cab, cmd)
                cmd_nome = "ABRIR PORTA" if cmd == 1 else "FECHAR PORTA"
                print("\n" + "-" * 55)
                print(f"[SUCESSO] Comando '{cmd_nome}' enviado à Cabine {cab} (Offset 3 = {cmd}).")
                print("  • O eco de escrita 0x10 foi confirmado pelo simulador.")
                print("  • Dica: Selecione a Opção 1 para ver a transição de estado da porta.")
                print("-" * 55)
            elif opcao == "3":
                predio = p3.le_estado_predio()
                formatar_painel_predio(predio)
            elif opcao == "4":
                t = float(input("Temperatura ambiente (°C, ex: 25.0): ").strip())
                p = int(input("Pressão ambiente (hPa, ex: 1013): ").strip())
                p3.escreve_condicao_contorno(t, p)
                print("\n" + "-" * 55)
                print("[SUCESSO] Condição de contorno gravada com sucesso!")
                print(f"  • Registrador 5 (Temperatura): {t:.1f} °C (valor bruto: {round(t*10)})")
                print(f"  • Registrador 6 (Pressão):     {p} hPa")
                print("  • Efeito: Watchdog de ambiente renovado (wdt -> 0) e barramento")
                print("            de energia restabelecido para até 12000 mA.")
                print("-" * 55)
            elif opcao == "5":
                chamada = p3.le_chamada_da_fila()
                print("\n" + "-" * 55)
                print("[FILA DE DESTINO DO EDIFÍCIO]")
                print(f"  • Chamadas Pendentes: {chamada['pendentes']}")
                if chamada["pendentes"] > 0:
                    print(f"  • ID da Chamada:      #{chamada['id']}")
                    print(f"  • Trajeto:            Andar {chamada['origem']} ➔ Andar {chamada['destino']}")
                    print("  • Próximo passo:      Use a Opção 6 para atribuir a uma cabine.")
                else:
                    print("  • Status da Fila:     Vazia no momento.")
                print("-" * 55)
            elif opcao == "6":
                cid = int(input("ID da Chamada a atribuir: ").strip())
                cab = int(input("Cabine escolhida (1, 2 ou 3): ").strip())
                p3.atribui_chamada(cid, cab)
                print("\n" + "-" * 55)
                print(f"[SUCESSO] Chamada #{cid} atribuída à Cabine {cab}!")
                print(f"  • Escrito ID={cid} no Reg 9 e Cabine={cab} no Reg 10.")
                print("  • Próximo passo: Use a Opção 7 para remover (pop) da fila.")
                print("-" * 55)
            elif opcao == "7":
                p3.remove_chamada_da_fila()
                print("\n" + "-" * 55)
                print("[SUCESSO] Chamada da cabeça da fila removida (Pop = 1 no Reg 4).")
                print("  • O buffer do simulador avançou para a próxima chamada.")
                print("-" * 55)
            elif opcao == "8":
                addr = int(input("Endereço do dispositivo (ex: 0x11 ou 17): ").strip(), 0)
                reg = int(input("Registrador inicial (offset): ").strip())
                qtd = int(input("Quantidade de registradores: ").strip())
                valores = p3.read_holding_registers(addr, reg, qtd)
                print("\n" + "-" * 55)
                print(f"[LEITURA 0x03 CONCLUÍDA - Dispositivo 0x{addr:02X}]")
                print(f"  • Offset Inicial: {reg} | Quantidade: {qtd}")
                print(f"  • Lista Bruta:    {valores}")
                print("  • Registradores decodificados:")
                for i, v in enumerate(valores):
                    print(f"    └─ Offset {reg + i:2d}: {v:6d}  (hex: 0x{v:04X})")
                print("-" * 55)
            elif opcao == "9":
                addr = int(input("Endereço do dispositivo (ex: 0x20 ou 32): ").strip(), 0)
                reg = int(input("Registrador inicial: ").strip())
                vals_str = input("Valores separados por vírgula (ex: 253, 1013): ").strip()
                valores = [int(x.strip()) for x in vals_str.split(",")]
                p3.write_multiple_registers(addr, reg, valores)
                print("\n" + "-" * 55)
                print(f"[ESCRITA 0x10 CONCLUÍDA - Dispositivo 0x{addr:02X}]")
                print(f"  • Offset Inicial: {reg} | Qtd: {len(valores)}")
                print(f"  • Valores:        {valores}")
                print("  • Confirmação:    Eco de escrita validado com sucesso!")
                print("-" * 55)
            elif opcao == "10":
                print("\n" + "-" * 55)
                print("[TESTE DE EXCEÇÃO MODBUS DIDÁTICO]")
                print("Tentando escrever no registrador 0 da Cabine 1 (registrador Read-Only)...")
                try:
                    p3.write_multiple_registers(0x11, reg_inicial=0, valores=[999])
                    print("[FALHA DO TESTE] A escrita foi aceita quando deveria ter sido rejeitada!")
                except RuntimeError as ex:
                    print("\n-> EXCEÇÃO CAPTURADA COM SUCESSO:")
                    print(f"   {ex}")
                    print("\n-> EXPLICAÇÃO DIDÁTICA:")
                    print("   O registrador 0 é somente-leitura. O simulador ESP32 detectou a")
                    print("   violação e respondeu com o código de exceção MODBUS 0x02.")
                    print("   O sistema tratou a exceção sem retentar indevidamente.")
                print("-" * 55)
            elif opcao == "11":
                monitoramento_continuo(p3)
            elif opcao == "0":
                break
            else:
                print("[AVISO] Opção inválida.")
        except KeyboardInterrupt:
            print("\nOperação cancelada pelo usuário.")
            break
        except Exception as e:  # noqa: BLE001
            print(f"[FALHA NA OPERAÇÃO] {e}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CLI da Entrega 2 - FSE 2026/2 (UART & MODBUS)"
    )
    parser.add_argument(
        "porta",
        nargs="?",
        default=UART_PORTA_PADRAO,
        help="Porta serial UART (padrão: /dev/serial0)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Força a execução com FakeUartDriver (simulador offline)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=UART_TIMEOUT_PADRAO,
        help="Timeout em segundos (padrão: 0.5)",
    )
    parser.add_argument(
        "--matricula",
        type=str,
        default=None,
        help="6 últimos dígitos da matrícula (ex: 038442)",
    )
    args = parser.parse_args()

    # Valida matrícula configurada
    try:
        mat_bytes = validar_e_obter_matricula(args.matricula)
        mat_str = "".join(str(b) for b in mat_bytes)
    except Exception as e:  # noqa: BLE001
        print(f"[ERRO DE CONFIGURAÇÃO] {e}")
        sys.exit(1)

    driver = criar_uart_driver(
        porta=args.porta, timeout=args.timeout, forcar_mock=args.mock
    )
    p1 = ProtocoloSimplificado(driver, matricula=mat_bytes)
    p2 = ProtocoloModbusP2(driver, matricula=mat_bytes)
    p3 = SimuladorModbusClient(driver, matricula=mat_bytes)

    print("\n" + "#" * 60)
    print("   FUNDAMENTOS DE SISTEMAS EMBARCADOS — TRABALHO 1")
    print("   ENTREGA 2: COMUNICAÇÃO UART (SIMPLIFICADO + MODBUS)")
    print("#" * 60)
    print(f"  Porta UART: {driver.porta}")
    print(f"  Timeout:    {driver.timeout:.2f} s")
    print(f"  Matrícula:  {mat_str} (Bytes crus: {list(mat_bytes)})")
    print(
        f"  Modo:       {'MOCK SIMULADO' if isinstance(driver.porta, str) and 'FAKE' in driver.porta else 'HARDWARE'}"
    )
    print("#" * 60)

    try:
        while True:
            print("\n--- MENU PRINCIPAL ---")
            print("1. Parte 1 — Protocolo Simplificado (6 comandos)")
            print("2. Parte 2 — Wrapper MODBUS Didático (6 comandos)")
            print("3. Parte 3 — Simulador de Elevadores (0x03 e 0x10)")
            print("0. Sair")
            opcao = input("\nSelecione o módulo: ").strip()

            if opcao == "1":
                menu_parte1(p1)
            elif opcao == "2":
                menu_parte2(p2)
            elif opcao == "3":
                menu_parte3(p3)
            elif opcao == "0":
                print("Encerrando aplicação...")
                break
            else:
                print("[AVISO] Opção inválida.")
    except KeyboardInterrupt:
        print(
            "\n\nSinal SIGINT (Ctrl+C) recebido. Encerrando aplicação de forma limpa..."
        )
    finally:
        driver.fechar()
        print("Comunicação serial finalizada com sucesso.")


if __name__ == "__main__":
    main()
