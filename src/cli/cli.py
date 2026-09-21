"""Interface de Linha de Comando (CLI) para controle interativo da Cabine 1."""

import sys
import threading
import time
from typing import List

from ..config import MotorDirection
from ..controller.elevator import ElevatorController


class Cli:
    """Interpretador de comandos interativos do terminal."""

    @staticmethod
    def print_banner() -> None:
        print(
            "\n--- CONTROLE DA CABINE 1 (FSE) ---\n"
            "Comandos disponíveis:\n"
            "  andar <0|1|2> (ou 0, 1, 2)  Desloca para o andar especificado\n"
            "  homing                      Calibra e zera automaticamente no piso térreo\n"
            "  motor <dir> <duty>          Acionamento direto (livre|subir|descer|freio, 0-100)\n"
            "  status                      Exibe telemetria de sensores e atuadores\n"
            "  zerar [cota_ou_andar]       Redefine a cota de posição de referência\n"
            "  parar                       Interrompe movimento e aciona freio\n"
            "  ajuda                       Exibe lista de comandos\n"
            "  sair                        Encerra a aplicação\n"
            "----------------------------------\n"
        )

    @classmethod
    def run_loop(cls, controller: ElevatorController, running_event: threading.Event) -> None:
        """Executa o loop interativo de comando."""
        cls.print_banner()

        while running_event.is_set():
            try:
                sys.stdout.write("elevador> ")
                sys.stdout.flush()

                line = sys.stdin.readline()
                if not line:
                    break

                line = line.strip()
                if not line:
                    continue

                tokens: List[str] = line.split()
                cmd = tokens[0].lower()

                if cmd in ("0", "1", "2"):
                    controller.ir_para_andar(int(cmd))

                elif cmd in ("andar", "ir") or (cmd.startswith("andar") and len(cmd) > 5 and cmd[5:] in ("0", "1", "2")):
                    if cmd in ("andar", "ir"):
                        if len(tokens) < 2:
                            print("[ERRO] Sintaxe: andar <0|1|2>")
                            continue
                        andar_str = tokens[1]
                    else:
                        andar_str = cmd[5:]

                    try:
                        andar = int(andar_str)
                        if andar in (0, 1, 2):
                            controller.ir_para_andar(andar)
                        else:
                            print(f"[ERRO] Andar inválido: '{andar_str}'. Valores permitidos: 0, 1, 2.")
                    except ValueError:
                        print(f"[ERRO] Andar inválido: '{andar_str}'. Valores permitidos: 0, 1, 2.")

                elif cmd == "homing":
                    controller.executar_homing()

                elif cmd in ("zerar", "calibrar"):
                    pos_calib = 0
                    if len(tokens) >= 2:
                        try:
                            val = int(tokens[1])
                            if val in (0, 1, 2):
                                pos_calib = val * 3000
                            else:
                                pos_calib = val
                        except ValueError:
                            print(f"[ERRO] Valor de calibração inválido: '{tokens[1]}'.")
                            continue
                    controller.recalibrar_posicao(pos_calib)

                elif cmd == "motor":
                    if len(tokens) < 3:
                        print("[ERRO] Sintaxe: motor <livre|subir|descer|freio> <duty_0_a_100>")
                        continue

                    dir_str = tokens[1].lower()
                    direcao_map = {
                        "livre": MotorDirection.LIVRE,
                        "subir": MotorDirection.SUBIR,
                        "descer": MotorDirection.DESCER,
                        "freio": MotorDirection.FREIO,
                    }

                    if dir_str not in direcao_map:
                        print(f"[ERRO] Direção inválida: '{tokens[1]}'. Valores permitidos: livre, subir, descer, freio.")
                        continue

                    try:
                        duty = float(tokens[2])
                        if 0.0 <= duty <= 100.0:
                            controller.comando_motor(direcao_map[dir_str], duty)
                        else:
                            print(f"[ERRO] Duty cycle fora da faixa [0, 100]: '{tokens[2]}'.")
                    except ValueError:
                        print(f"[ERRO] Duty cycle não numérico: '{tokens[2]}'.")

                elif cmd == "status":
                    controller.print_status()

                elif cmd == "parar":
                    controller.parar()

                elif cmd in ("ajuda", "help", "?"):
                    cls.print_banner()

                elif cmd in ("sair", "exit", "quit"):
                    print("Encerrando execução...")
                    running_event.clear()
                    break

                else:
                    print(f"[ERRO] Comando não reconhecido: '{cmd}'. Digite 'ajuda' para instruções.")

            except (KeyboardInterrupt, EOFError):
                break
            except Exception as e:
                print(f"[ERRO] Falha no processamento: {e}")

            time.sleep(0.01)

        try:
            controller.parar()
            controller.cleanup()
        except Exception:
            pass
