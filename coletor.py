"""
Coletor de dados: lê o CLP via Modbus TCP e grava no banco SQLite.

É a ponte entre o mundo da automação (TA/OT, o CLP) e o mundo da TI (o banco
de dados). A cada 2 segundos ele:
  1. pede os 4 registradores ao CLP (função Modbus 03),
  2. converte os inteiros de volta para valores reais (desfaz a escala),
  3. grava cada leitura no banco (uma série temporal: momento + tag + valor),
  4. verifica limites de alarme e registra quando algo sai da faixa.

Como rodar (com o simulador_clp.py já rodando em outro terminal):
    python coletor.py
"""

import socket
import sqlite3
import struct
import time
from datetime import datetime

HOST_CLP = "127.0.0.1"
PORTA_CLP = 5020
UNIDADE = 1               # endereço do escravo Modbus
INTERVALO_SEGUNDOS = 2
ARQUIVO_BANCO = "dados.db"

# Como interpretar cada registrador: endereço -> (tag, escala, unidade de medida)
MAPA_TAGS = [
    ("temperatura", 10, "°C"),
    ("pressao", 100, "bar"),
    ("vazao", 10, "Nm³/h"),
    ("metano", 10, "%"),
]

# Limites de alarme: tag -> (mínimo aceitável, máximo aceitável)
LIMITES = {
    "temperatura": (25.0, 50.0),
    "pressao": (6.0, 9.0),
    "vazao": (80.0, 170.0),
    "metano": (90.0, 100.0),  # biometano precisa de alto teor de metano
}


def criar_banco(conexao):
    """Cria as tabelas se ainda não existirem."""
    conexao.executescript("""
        CREATE TABLE IF NOT EXISTS leituras (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            momento  TEXT NOT NULL,
            tag      TEXT NOT NULL,
            valor    REAL NOT NULL,
            unidade  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_leituras_tag_momento ON leituras (tag, momento);

        CREATE TABLE IF NOT EXISTS alarmes (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            momento   TEXT NOT NULL,
            tag       TEXT NOT NULL,
            valor     REAL NOT NULL,
            mensagem  TEXT NOT NULL
        );
    """)
    conexao.commit()


def receber_exato(sock, tamanho):
    """Lê exatamente 'tamanho' bytes do socket."""
    dados = b""
    while len(dados) < tamanho:
        pedaco = sock.recv(tamanho - len(dados))
        if not pedaco:
            raise ConnectionError("CLP fechou a conexão")
        dados += pedaco
    return dados


def ler_holding_registers(sock, id_transacao, inicio, quantidade):
    """Envia um pedido Modbus TCP (função 03) e devolve a lista de valores lidos."""
    # PDU do pedido: função (1 byte) + endereço inicial (2) + quantidade (2)
    pdu = struct.pack(">BHH", 0x03, inicio, quantidade)
    # Cabeçalho MBAP: id da transação + protocolo 0 + tamanho + unidade
    mbap = struct.pack(">HHHB", id_transacao, 0, len(pdu) + 1, UNIDADE)
    sock.sendall(mbap + pdu)

    cabecalho = receber_exato(sock, 7)
    id_resp, _, tamanho, _ = struct.unpack(">HHHB", cabecalho)
    resposta = receber_exato(sock, tamanho - 1)

    if id_resp != id_transacao:
        raise ValueError("Resposta com id de transação diferente do pedido")
    if resposta[0] & 0x80:  # bit mais alto ligado = o CLP respondeu com erro
        raise ValueError(f"CLP respondeu com exceção Modbus código {resposta[1]}")

    qtd_bytes = resposta[1]
    return list(struct.unpack(f">{qtd_bytes // 2}H", resposta[2:2 + qtd_bytes]))


def verificar_alarme(tag, valor):
    """Devolve uma mensagem de alarme se o valor estiver fora da faixa, senão None."""
    minimo, maximo = LIMITES[tag]
    if valor < minimo:
        return f"{tag} BAIXA: {valor} (mínimo {minimo})"
    if valor > maximo:
        return f"{tag} ALTA: {valor} (máximo {maximo})"
    return None


def main():
    conexao = sqlite3.connect(ARQUIVO_BANCO)
    criar_banco(conexao)
    id_transacao = 0

    print(f"[COLETOR] Conectando ao CLP em {HOST_CLP}:{PORTA_CLP}...")
    while True:
        try:
            with socket.create_connection((HOST_CLP, PORTA_CLP), timeout=5) as sock:
                print("[COLETOR] Conectado! Coletando a cada", INTERVALO_SEGUNDOS, "s. Ctrl+C para parar.")
                while True:
                    id_transacao = (id_transacao + 1) % 65536
                    brutos = ler_holding_registers(sock, id_transacao, 0, len(MAPA_TAGS))
                    momento = datetime.now().isoformat(timespec="seconds")

                    partes = []
                    for (tag, escala, unidade), bruto in zip(MAPA_TAGS, brutos):
                        valor = round(bruto / escala, 2)
                        conexao.execute(
                            "INSERT INTO leituras (momento, tag, valor, unidade) VALUES (?, ?, ?, ?)",
                            (momento, tag, valor, unidade),
                        )
                        partes.append(f"{tag}={valor}{unidade}")

                        alarme = verificar_alarme(tag, valor)
                        if alarme:
                            conexao.execute(
                                "INSERT INTO alarmes (momento, tag, valor, mensagem) VALUES (?, ?, ?, ?)",
                                (momento, tag, valor, alarme),
                            )
                            print(f"  ⚠ ALARME: {alarme}")

                    conexao.commit()
                    print(f"[{momento}] " + "  ".join(partes))
                    time.sleep(INTERVALO_SEGUNDOS)

        except (ConnectionError, OSError, ValueError) as erro:
            # Em planta real a comunicação cai; o coletor não pode morrer por isso.
            print(f"[COLETOR] Falha de comunicação ({erro}). Tentando de novo em 5 s...")
            time.sleep(5)
        except KeyboardInterrupt:
            print("\n[COLETOR] Encerrado.")
            break

    conexao.close()


if __name__ == "__main__":
    main()
