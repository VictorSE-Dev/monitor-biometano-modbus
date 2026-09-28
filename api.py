"""
API REST + painel web para consultar os dados coletados.

Lê o banco SQLite preenchido pelo coletor e disponibiliza os dados em JSON,
para que outros sistemas (um MES, um ERP, um dashboard) possam consumir.

Rotas:
    GET /                              -> painel web simples (atualiza sozinho)
    GET /api/atual                     -> último valor de cada tag
    GET /api/historico?tag=pressao&limite=20
                                       -> últimas leituras de uma tag
    GET /api/resumo                    -> mínimo, máximo e média de cada tag
    GET /api/alarmes?limite=20         -> últimos alarmes registrados

Como rodar:  python api.py   e abra http://127.0.0.1:8000 no navegador
"""

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HOST = "127.0.0.1"
PORTA = 8000
ARQUIVO_BANCO = "dados.db"
TAGS_VALIDAS = {"temperatura", "pressao", "vazao", "metano"}


def consultar(sql, parametros=()):
    """Executa uma consulta e devolve uma lista de dicionários."""
    conexao = sqlite3.connect(ARQUIVO_BANCO)
    conexao.row_factory = sqlite3.Row
    try:
        return [dict(linha) for linha in conexao.execute(sql, parametros)]
    finally:
        conexao.close()


def valores_atuais():
    return consultar("""
        SELECT l.tag, l.valor, l.unidade, l.momento
        FROM leituras l
        JOIN (SELECT tag, MAX(id) AS ultimo FROM leituras GROUP BY tag) u
          ON l.id = u.ultimo
        ORDER BY l.tag
    """)


def historico(tag, limite):
    return consultar(
        "SELECT momento, valor, unidade FROM leituras WHERE tag = ? ORDER BY id DESC LIMIT ?",
        (tag, limite),
    )


def resumo():
    return consultar("""
        SELECT tag, unidade,
               COUNT(*)             AS leituras,
               ROUND(MIN(valor), 2) AS minimo,
               ROUND(MAX(valor), 2) AS maximo,
               ROUND(AVG(valor), 2) AS media
        FROM leituras GROUP BY tag, unidade ORDER BY tag
    """)


def alarmes(limite):
    return consultar(
        "SELECT momento, tag, valor, mensagem FROM alarmes ORDER BY id DESC LIMIT ?",
        (limite,),
    )


PAINEL_HTML = """<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Monitor Biometano</title>
<style>
  body { font-family: system-ui, sans-serif; background:#0f172a; color:#e2e8f0; margin:0; padding:24px; }
  h1 { font-size:20px; margin:0 0 4px; } p { color:#94a3b8; margin:0 0 20px; }
  .grade { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; }
  .card { background:#1e293b; border-radius:10px; padding:16px; }
  .tag { color:#94a3b8; font-size:13px; text-transform:uppercase; letter-spacing:.05em; }
  .valor { font-size:30px; font-weight:600; margin-top:6px; }
  h2 { font-size:16px; margin:28px 0 8px; }
  ul { list-style:none; padding:0; margin:0; font-size:14px; }
  li { padding:6px 0; border-bottom:1px solid #1e293b; color:#fbbf24; }
</style></head>
<body>
  <h1>Monitor da Planta de Biometano</h1>
  <p>Dados lidos do CLP via Modbus TCP, atualizados a cada 2 segundos.</p>
  <div class="grade" id="cards"></div>
  <h2>Últimos alarmes</h2>
  <ul id="alarmes"></ul>
<script>
async function atualizar() {
  try {
    const atual = await (await fetch('/api/atual')).json();
    document.getElementById('cards').innerHTML = atual.map(t =>
      `<div class="card"><div class="tag">${t.tag}</div>
       <div class="valor">${t.valor} ${t.unidade}</div></div>`).join('');
    const al = await (await fetch('/api/alarmes?limite=5')).json();
    document.getElementById('alarmes').innerHTML = al.length
      ? al.map(a => `<li>${a.momento.replace('T',' ')} — ${a.mensagem}</li>`).join('')
      : '<li style="color:#4ade80">Nenhum alarme.</li>';
  } catch (e) { console.error(e); }
}
atualizar(); setInterval(atualizar, 2000);
</script>
</body></html>"""


class ManipuladorAPI(BaseHTTPRequestHandler):

    def responder_json(self, dados, status=200):
        corpo = json.dumps(dados, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        url = urlparse(self.path)
        params = parse_qs(url.query)
        texto_limite = params.get("limite", ["20"])[0]
        limite = min(int(texto_limite), 1000) if texto_limite.isdigit() else 20

        try:
            if url.path == "/":
                corpo = PAINEL_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)
            elif url.path == "/api/atual":
                self.responder_json(valores_atuais())
            elif url.path == "/api/historico":
                tag = params.get("tag", [""])[0]
                if tag not in TAGS_VALIDAS:
                    self.responder_json({"erro": f"tag inválida, use uma de: {sorted(TAGS_VALIDAS)}"}, 400)
                else:
                    self.responder_json(historico(tag, limite))
            elif url.path == "/api/resumo":
                self.responder_json(resumo())
            elif url.path == "/api/alarmes":
                self.responder_json(alarmes(limite))
            else:
                self.responder_json({"erro": "rota não encontrada"}, 404)
        except sqlite3.OperationalError:
            self.responder_json({"erro": "banco ainda não existe — rode o coletor.py primeiro"}, 503)

    def log_message(self, formato, *args):
        print(f"[API] {self.address_string()} {formato % args}")


def main():
    servidor = ThreadingHTTPServer((HOST, PORTA), ManipuladorAPI)
    print(f"[API] Rodando em http://{HOST}:{PORTA}  (Ctrl+C para parar)")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\n[API] Encerrada.")


if __name__ == "__main__":
    main()
