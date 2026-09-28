"""
Simulador de CLP (Controlador Lógico Programável) via Modbus TCP.

Imita um CLP de uma planta de biometano: a cada segundo ele atualiza
4 "holding registers" com leituras de sensores e responde a quem pedir
esses valores pelo protocolo Modbus TCP (função 03).

Mapa de registradores (valores inteiros de 16 bits, com escala):
    Endereço 0 -> Temperatura (°C)      valor x 10   (ex.: 355 = 35,5 °C)
    Endereço 1 -> Pressão (bar)         valor x 100  (ex.: 742 = 7,42 bar)
    Endereço 2 -> Vazão (Nm³/h)         valor x 10   (ex.: 1204 = 120,4 Nm³/h)
    Endereço 3 -> Teor de metano (%)    valor x 10   (ex.: 962 = 96,2 %)

Por que escala? Registradores Modbus só guardam números inteiros de 0 a 65535.
Para mandar casas decimais, o CLP multiplica o valor e quem lê divide de volta.

Como rodar:  python simulador_clp.py
"""

import random
import socketserver
import struct
import threading
import time

HOST = "127.0.0.1"
PORTA = 5020  # a porta padrão do Modbus é 502, mas ela exige permissão de administrador

# Cada sensor: (valor inicial, mínimo, máximo, variação máxima por segundo, escala)
SENSORES = {
    0: {"nome": "temperatura", "valor": 35.0, "min": 20.0, "max": 60.0, "passo": 0.4, "escala": 10},
    1: {"nome": "pressao", "valor": 7.5, "min": 5.0, "max": 10.0, "passo": 0.15, "escala": 100},
    2: {"nome": "vazao", "valor": 120.0, "min": 60.0, "max": 180.0, "passo": 3.0, "escala": 10},
    3: {"nome": "metano", "valor": 96.0, "min": 85.0, "max": 99.0, "passo": 0.3, "escala": 10},
}

# Memória do CLP: lista de registradores (inteiros de 16 bits)
registradores = [0] * len(SENSORES)
trava = threading.Lock()  # evita ler e escrever os registradores ao mesmo tempo

# Códigos de função e de exceção do Modbus
FUNCAO_LER_HOLDING = 0x03
ERRO_FUNCAO_INVALIDA = 0x01
ERRO_ENDERECO_INVALIDO = 0x02


def atualizar_sensores():
    """Simula os sensores mudando aos poucos (passeio aleatório), para sempre."""
    while True:
        with trava:
            for endereco, s in SENSORES.items():
                s["valor"] += random.uniform(-s["passo"], s["passo"])
                s["valor"] = max(s["min"], min(s["max"], s["valor"]))
                registradores[endereco] = int(round(s["valor"] * s["escala"]))
        time.sleep(1)


def receber_exato(sock, tamanho):
    """Lê exatamente 'tamanho' bytes do socket (o TCP pode entregar em pedaços)."""
    dados = b""
    while len(dados) < tamanho:
        pedaco = sock.recv(tamanho - len(dados))
        if not pedaco:
            raise ConnectionError("conexão fechada")
        dados += pedaco
    return dados


def montar_resposta(id_transacao, unidade, pdu):
    """Monta o cabeçalho MBAP do Modbus TCP na frente da resposta (PDU)."""
    # MBAP = id da transação (2 bytes) + protocolo 0 (2) + tamanho (2) + unidade (1)
    cabecalho = struct.pack(">HHHB", id_transacao, 0, len(pdu) + 1, unidade)
    return cabecalho + pdu


class ManipuladorModbus(socketserver.BaseRequestHandler):
    """Atende um cliente conectado, respondendo cada pedido Modbus que chegar."""

    def handle(self):
        print(f"[CLP] Cliente conectado: {self.client_address[0]}")
        try:
            while True:
                # 1) Lê o cabeçalho MBAP (7 bytes)
                cabecalho = receber_exato(self.request, 7)
                id_transacao, protocolo, tamanho, unidade = struct.unpack(">HHHB", cabecalho)

                # 2) Lê o resto da mensagem (o PDU): código da função + dados
                pdu = receber_exato(self.request, tamanho - 1)
                funcao = pdu[0]

                if funcao != FUNCAO_LER_HOLDING:
                    resposta = struct.pack(">BB", funcao | 0x80, ERRO_FUNCAO_INVALIDA)
                else:
                    inicio, quantidade = struct.unpack(">HH", pdu[1:5])
                    if quantidade < 1 or inicio + quantidade > len(registradores):
                        resposta = struct.pack(">BB", funcao | 0x80, ERRO_ENDERECO_INVALIDO)
                    else:
                        with trava:
                            valores = registradores[inicio:inicio + quantidade]
                        # Resposta: função + quantidade de bytes + valores (2 bytes cada)
                        resposta = struct.pack(">BB", funcao, quantidade * 2)
                        resposta += struct.pack(f">{quantidade}H", *valores)

                self.request.sendall(montar_resposta(id_transacao, unidade, resposta))
        except ConnectionError:
            print(f"[CLP] Cliente desconectado: {self.client_address[0]}")


class ServidorModbus(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    threading.Thread(target=atualizar_sensores, daemon=True).start()
    with ServidorModbus((HOST, PORTA), ManipuladorModbus) as servidor:
        print(f"[CLP] Simulador Modbus TCP rodando em {HOST}:{PORTA}")
        print("[CLP] Registradores: 0=temperatura  1=pressao  2=vazao  3=metano")
        print("[CLP] Ctrl+C para parar.")
        try:
            servidor.serve_forever()
        except KeyboardInterrupt:
            print("\n[CLP] Encerrado.")


if __name__ == "__main__":
    main()
