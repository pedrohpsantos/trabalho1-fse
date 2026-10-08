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


def menu_parte1(p1: ProtocoloSimplificado) -> None:
    """Menu interativo dos 6 comandos da Parte 1 (Protocolo Simplificado)."""
    while True:
        print("\n" + "=" * 50)
        print("   PARTE 1 — PROTOCOLO SIMPLIFICADO (SEM CRC)")
        print("=" * 50)
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
                print(f"[SUCESSO] Valor inteiro: {val}")
            elif opcao == "2":
                val = p1.solicita_float()
                print(f"[SUCESSO] Valor float: {val:.4f}")
            elif opcao == "3":
                val = p1.solicita_string()
                print(f"[SUCESSO] String: '{val}'")
            elif opcao == "4":
                entrada = input("Digite o valor inteiro a enviar (ex: 3245): ").strip()
                val = p1.envia_inteiro(int(entrada))
                print(f"[SUCESSO] Resposta do dispositivo: {val}")
            elif opcao == "5":
                entrada = input("Digite o valor float a enviar (ex: 12.75): ").strip()
                val = p1.envia_float(float(entrada))
                print(f"[SUCESSO] Resposta do dispositivo: {val:.4f}")
            elif opcao == "6":
                entrada = input("Digite o texto a enviar: ").strip()
                val = p1.envia_string(entrada)
                print(f"[SUCESSO] Resposta do dispositivo: '{val}'")
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
    while True:
        print("\n" + "=" * 50)
        print("   PARTE 2 — MODBUS MODIFICADO (WRAPPER 0x23 / 0x16)")
        print("=" * 50)
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
                print(f"[SUCESSO] Valor inteiro: {val}")
            elif opcao == "2":
                val = p2.solicita_float()
                print(f"[SUCESSO] Valor float: {val:.4f}")
            elif opcao == "3":
                val = p2.solicita_string()
                print(f"[SUCESSO] String: '{val}'")
            elif opcao == "4":
                entrada = input("Digite o valor inteiro a enviar (ex: 3245): ").strip()
                val = p2.envia_inteiro(int(entrada))
                print(f"[SUCESSO] Resposta MODBUS: {val}")
            elif opcao == "5":
                entrada = input("Digite o valor float a enviar (ex: 45.8): ").strip()
                val = p2.envia_float(float(entrada))
                print(f"[SUCESSO] Resposta MODBUS: {val:.4f}")
            elif opcao == "6":
                entrada = input("Digite o texto a enviar: ").strip()
                val = p2.envia_string(entrada)
                print(f"[SUCESSO] Resposta MODBUS: '{val}'")
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
    print("\n[INICIANDO MONITORAMENTO CONTÍNUO A 1 Hz]")
    print("Pressione Ctrl+C a qualquer momento para interromper...\n")
    try:
        while True:
            c1 = p3.le_estado_cabine(1)
            pr = p3.le_estado_predio()

            print(
                f"[CABINE 1] Andar: {c1['andar_atual']} | Pos: {c1['posicao_mm']:>5} mm | "
                f"Nivelado: {c1['nivelado']!s:<5} | Porta: {c1['porta_estado']:<8} | "
                f"Passageiros: {c1['passageiros']} || "
                f"[PRÉDIO] Barramento: {pr['barramento_max_ma']} mA | WD: {pr['watchdog_ambiente']} | "
                f"Fila: {pr['chamadas_na_fila']}"
            )
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[MONITORAMENTO INTERROMPIDO]")


def menu_parte3(p3: SimuladorModbusClient) -> None:
    """Menu interativo das operações do simulador (Parte 3)."""
    while True:
        print("\n" + "=" * 50)
        print("   PARTE 3 — SIMULADOR DE ELEVADORES (0x03 E 0x10)")
        print("=" * 50)
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
                print("\n--- Estado da Cabine ---")
                for k, v in estado.items():
                    print(f"  {k}: {v}")
            elif opcao == "2":
                cab = int(input("Informe a cabine (1, 2 ou 3): ").strip())
                cmd = int(input("Comando (1=Abrir, 2=Fechar): ").strip())
                p3.comanda_porta(cab, cmd)
                print(f"[SUCESSO] Comando {cmd} enviado à Cabine {cab}.")
            elif opcao == "3":
                predio = p3.le_estado_predio()
                print("\n--- Estado do Prédio ---")
                for k, v in predio.items():
                    print(f"  {k}: {v}")
            elif opcao == "4":
                t = float(input("Temperatura ambiente (°C, ex: 25.3): ").strip())
                p = int(input("Pressão ambiente (hPa, ex: 1013): ").strip())
                p3.escreve_condicao_contorno(t, p)
                print("[SUCESSO] Condição de contorno enviada com sucesso.")
            elif opcao == "5":
                chamada = p3.le_chamada_da_fila()
                print(
                    f"\n[FILA DE DESTINO] Pendentes: {chamada['pendentes']} | "
                    f"Origem: Andar {chamada['origem']} | "
                    f"Destino: Andar {chamada['destino']} | ID: #{chamada['id']}"
                )
            elif opcao == "6":
                cid = int(input("ID da Chamada a atribuir: ").strip())
                cab = int(input("Cabine escolhida (1, 2 ou 3): ").strip())
                p3.atribui_chamada(cid, cab)
                print(f"[SUCESSO] Chamada #{cid} atribuída à Cabine {cab}.")
            elif opcao == "7":
                p3.remove_chamada_da_fila()
                print("[SUCESSO] Chamada da cabeça da fila removida (pop=1).")
            elif opcao == "8":
                addr = int(
                    input("Endereço do dispositivo (ex: 0x11 ou 17): ").strip(), 0
                )
                reg = int(input("Registrador inicial (offset): ").strip())
                qtd = int(input("Quantidade de registradores: ").strip())
                valores = p3.read_holding_registers(addr, reg, qtd)
                print(f"[SUCESSO] Valores lidos: {valores}")
            elif opcao == "9":
                addr = int(
                    input("Endereço do dispositivo (ex: 0x20 ou 32): ").strip(), 0
                )
                reg = int(input("Registrador inicial: ").strip())
                vals_str = input(
                    "Valores separados por vírgula (ex: 253, 1013): "
                ).strip()
                valores = [int(x.strip()) for x in vals_str.split(",")]
                p3.write_multiple_registers(addr, reg, valores)
                print("[SUCESSO] Escrita concluída com confirmação de eco.")
            elif opcao == "10":
                print(
                    "Disparando escrita no registrador 0 da Cabine 1 (registrador Read-Only)..."
                )
                try:
                    p3.write_multiple_registers(0x11, reg_inicial=0, valores=[999])
                except RuntimeError as ex:
                    print(f"-> Resposta de exceção capturada com sucesso: {ex}")
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
        help="6 últimos dígitos da matrícula (ex: 654321)",
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
