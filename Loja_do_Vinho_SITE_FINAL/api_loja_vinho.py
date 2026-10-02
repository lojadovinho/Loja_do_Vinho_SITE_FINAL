# Ponte segura entre o site da Loja do Vinho e o banco pdv_local.db
# Fase 1: catálogo + estoque + destaque regional + criação de pedido com baixa atômica.
# Rode SOMENTE em máquina/servidor protegido. Nunca publique o arquivo .db diretamente.

from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import sqlite3, json, os, re, mimetypes, secrets
from datetime import datetime
from PIL import Image
from io import BytesIO
import qrcode

BASE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("LOJA_VINHO_DB", BASE / "pdv_local.db"))
IMAGE_DIR = Path(os.environ.get("LOJA_VINHO_IMAGES", BASE / "imagens_produtos"))
HOST = os.environ.get("LOJA_VINHO_HOST", "127.0.0.1")
PORT = int(os.environ.get("LOJA_VINHO_PORT", "8787"))
ADMIN_TOKEN = os.environ.get("LOJA_VINHO_ADMIN_TOKEN", "")

IMAGE_DIR.mkdir(parents=True, exist_ok=True)

REGIONAL_CATEGORIES = {
    "LICOR ARRAIA DO QUIABO", "MELAÇO", "ARTESANATO",
}
REGIONAL_KEYWORDS = [
    "ARRAIA DO QUIABO", "GRANDVALLE", "GRAND VALLE", "RIO SOL",
    "DO VALLE", "VALE DO SÃO FRANCISCO", "VALE DO SAO FRANCISCO",
    "SÃO FRANCISCO", "SAO FRANCISCO", "JUAZEIRO", "PETROLINA",
    "UAUA", "Uauá", "CHEF GUIMARAES", "CHEF GUIMARÃES",
]


def ensure_site_tables(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS produto_site (
        codigo_barras TEXT PRIMARY KEY,
        destaque_site INTEGER NOT NULL DEFAULT 0,
        destaque_regional INTEGER NOT NULL DEFAULT 0,
        destaque_promocao INTEGER NOT NULL DEFAULT 0,
        ordem_destaque INTEGER NOT NULL DEFAULT 9999,
        descricao_site TEXT,
        atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS produto_imagens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo_barras TEXT NOT NULL,
        url TEXT NOT NULL,
        tipo TEXT NOT NULL DEFAULT 'principal',
        ordem INTEGER NOT NULL DEFAULT 0,
        alt_text TEXT,
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_produto_imagens_codigo ON produto_imagens(codigo_barras);
    CREATE TABLE IF NOT EXISTS pedidos_online (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT NOT NULL DEFAULT 'AGUARDANDO_PAGAMENTO',
        cliente TEXT, endereco TEXT, bairro TEXT,
        frete REAL NOT NULL DEFAULT 0, desconto REAL NOT NULL DEFAULT 0, total REAL NOT NULL DEFAULT 0,
        forma_pagamento TEXT DEFAULT 'PIX', itens_json TEXT NOT NULL, venda_id INTEGER, observacao TEXT,
        FOREIGN KEY (venda_id) REFERENCES vendas(id)
    );
    CREATE INDEX IF NOT EXISTS idx_pedidos_online_status ON pedidos_online(status);
    CREATE INDEX IF NOT EXISTS idx_pedidos_online_criado ON pedidos_online(criado_em);
    """)


def db():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def regional(row):
    cat = str(row["categoria"] or "").upper()
    nome = str(row["nome"] or "").upper()
    return cat in REGIONAL_CATEGORIES or any(k.upper() in nome for k in REGIONAL_KEYWORDS)


def image_url(code):
    p = IMAGE_DIR / f"{code}.webp"
    return f"/imagens/{code}.webp" if p.exists() else None


def product_from_row(r, stock=None):
    code = str(r["codigo_barras"])
    regional_db = bool(r["destaque_regional"]) if "destaque_regional" in r.keys() else False
    destaque = bool(r["destaque_site"]) if "destaque_site" in r.keys() else False
    promocao = bool(r["destaque_promocao"]) if "destaque_promocao" in r.keys() else False
    descricao_site = r["descricao_site"] if "descricao_site" in r.keys() else None
    return {
        "codigo_barras": code,
        "nome": r["nome"],
        "categoria": r["categoria"],
        "tipo": r["categoria"],
        "preco": float(r["preco_varejo"] or 0),
        "preco_varejo": float(r["preco_varejo"] or 0),
        "preco_atacado": float(r["preco_atacado"] or 0),
        "status": int(r["status"] or 0),
        "estoque": float(stock or 0),
        "foto": image_url(code),
        "regional": regional_db or regional(r),
        "destaque_site": destaque,
        "destaque_promocao": promocao,
        "ordem_destaque": int(r["ordem_destaque"] or 9999) if "ordem_destaque" in r.keys() else 9999,
        "descricao": descricao_site or "",
    }


PIX_KEY = os.environ.get("LOJA_VINHO_PIX_KEY", "74988154699")
PIX_MERCHANT = os.environ.get("LOJA_VINHO_PIX_NAME", "LOJA DO VINHO")[:25]
PIX_CITY = os.environ.get("LOJA_VINHO_PIX_CITY", "JUAZEIRO")[:15]

def pix_field(tag, value):
    value = str(value)
    return f"{tag}{len(value):02d}{value}"

def gerar_pix_payload(valor):
    valor = float(valor)
    if valor <= 0:
        raise ValueError("Valor PIX inválido")
    chave = re.sub(r"[^0-9A-Za-z+@._-]", "", PIX_KEY)
    merchant = pix_field("00", "BR.GOV.BCB.PIX") + pix_field("01", chave)
    payload = "000201" + pix_field("26", merchant) + "52040000" + "5303986"
    payload += pix_field("54", f"{valor:.2f}") + "5802BR" + pix_field("59", PIX_MERCHANT)
    payload += pix_field("60", PIX_CITY) + pix_field("62", pix_field("05", "***")) + "6304"
    crc = 0xFFFF
    for ch in payload.encode("utf-8"):
        crc ^= ch << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return payload + f"{crc:04X}"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def send_json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Admin-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Admin-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/admin.html":
            arquivo = BASE / "admin.html"
            if not arquivo.exists(): self.send_error(404); return
            raw = arquivo.read_bytes()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(raw))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(raw); return

        if u.path == "/":
            arquivo = BASE / "loja_vinho_v2.html"
            if not arquivo.exists(): self.send_error(404); return
            raw = arquivo.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
            return

        if u.path == "/api/saude":
            self.send_json({"ok": True, "servico": "loja-vinho-api", "hora": datetime.now().isoformat()})
            return

        if u.path.startswith("/imagens/"):
            code = Path(u.path).name
            if not re.fullmatch(r"[A-Za-z0-9._-]+\.webp", code):
                self.send_error(400); return
            p = IMAGE_DIR / code
            if not p.exists():
                self.send_error(404); return
            raw = p.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/webp")
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers(); self.wfile.write(raw)
            return

        if u.path == "/api/produtos":
            q = parse_qs(u.query)
            termo = (q.get("q", [""])[0] or "").strip().lower()
            categoria = (q.get("categoria", [""])[0] or "").strip()
            somente_regionais = q.get("regionais", ["0"])[0] == "1"
            try: limit = min(max(int(q.get("limit", ["120"])[0]), 1), 1000)
            except ValueError: limit = 120
            c = db()
            ensure_site_tables(c)
            sql = """
                SELECT p.codigo_barras,p.nome,p.categoria,p.preco_varejo,p.preco_atacado,p.status,
                       COALESCE(SUM(e.quantidade),0) estoque,
                       COALESCE(s.destaque_site,0) destaque_site, COALESCE(s.destaque_regional,0) destaque_regional,
                       COALESCE(s.destaque_promocao,0) destaque_promocao, COALESCE(s.ordem_destaque,9999) ordem_destaque,
                       s.descricao_site
                FROM produtos p LEFT JOIN produto_site s ON s.codigo_barras=p.codigo_barras LEFT JOIN estoque_saldos e ON e.codigo_barras=p.codigo_barras
                WHERE p.status=1
            """
            args=[]
            if categoria:
                sql += " AND UPPER(p.categoria)=UPPER(?)"; args.append(categoria)
            if termo:
                sql += " AND (LOWER(p.nome) LIKE ? OR LOWER(p.categoria) LIKE ? OR p.codigo_barras LIKE ?)"
                like=f"%{termo}%"; args += [like,like,f"%{termo}%"]
            sql += " GROUP BY p.codigo_barras ORDER BY p.nome LIMIT ?"; args.append(limit)
            if somente_regionais:
                # A filtragem regional depende também do nome do produto; por isso
                # buscamos o conjunto ativo e só depois aplicamos o limite.
                sql_sem_limit = sql.rsplit(" LIMIT ?", 1)[0]
                args_sem_limit = args[:-1]
                rows = c.execute(sql_sem_limit, args_sem_limit).fetchall()
            else:
                rows=c.execute(sql,args).fetchall()
            c.close()
            produtos=[product_from_row(r,r["estoque"]) for r in rows]
            if somente_regionais: produtos=[p for p in produtos if p["regional"]][:limit]
            self.send_json({"ok":True,"total":len(produtos),"produtos":produtos}); return

        if u.path == "/api/site/destaques":
            c=db(); ensure_site_tables(c)
            rows=c.execute("""SELECT p.codigo_barras,p.nome,p.categoria,p.preco_varejo,p.preco_atacado,p.status,
                              COALESCE(SUM(e.quantidade),0) estoque, COALESCE(s.destaque_site,0) destaque_site,
                              COALESCE(s.destaque_regional,0) destaque_regional, COALESCE(s.destaque_promocao,0) destaque_promocao,
                              COALESCE(s.ordem_destaque,9999) ordem_destaque, s.descricao_site
                       FROM produtos p LEFT JOIN produto_site s ON s.codigo_barras=p.codigo_barras
                       LEFT JOIN estoque_saldos e ON e.codigo_barras=p.codigo_barras
                       WHERE p.status=1 GROUP BY p.codigo_barras
                       HAVING estoque>0 AND (destaque_site=1 OR destaque_regional=1 OR destaque_promocao=1)
                       ORDER BY ordem_destaque,p.nome""").fetchall(); c.close()
            self.send_json({"ok":True,"produtos":[product_from_row(r,r["estoque"]) for r in rows]}); return

        if u.path == "/api/pix/qrcode":
            q = parse_qs(u.query)
            try: valor = float(q.get("valor", ["0"])[0])
            except ValueError: self.send_json({"ok":False,"erro":"Valor inválido"},400); return
            try:
                payload = gerar_pix_payload(valor)
                qr = qrcode.QRCode(version=None, box_size=8, border=2)
                qr.add_data(payload); qr.make(fit=True)
                img = qr.make_image()
                out = BytesIO(); img.save(out, format="PNG"); raw = out.getvalue()
            except Exception as e:
                self.send_json({"ok":False,"erro":str(e)},400); return
            self.send_response(200); self.send_header("Content-Type","image/png"); self.send_header("Cache-Control","no-store")
            self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw); return

        if u.path == "/api/admin/pedidos":
            if not self._authorized(): self.send_json({"ok":False,"erro":"Não autorizado"},401); return
            q = parse_qs(u.query); status=(q.get("status",[""])[0] or "").strip().upper()
            try: limit=min(max(int(q.get("limit",["100"])[0]),1),200)
            except ValueError: limit=100
            c=db(); ensure_site_tables(c)
            sql="SELECT * FROM pedidos_online"; args=[]
            if status: sql += " WHERE status=?"; args.append(status)
            sql += " ORDER BY id DESC LIMIT ?"; args.append(limit)
            rows=c.execute(sql,args).fetchall(); c.close(); pedidos=[]
            for r in rows:
                x=dict(r)
                try: x["itens"]=json.loads(x.pop("itens_json") or "[]")
                except Exception: x["itens"]=[]
                pedidos.append(x)
            self.send_json({"ok":True,"pedidos":pedidos}); return

        if u.path == "/api/categorias":
            c=db(); rows=c.execute("SELECT nome FROM categorias ORDER BY nome").fetchall(); c.close()
            self.send_json({"ok":True,"categorias":[r["nome"] for r in rows]}); return

        self.send_error(404)

    def _authorized(self):
        return bool(ADMIN_TOKEN) and secrets.compare_digest(self.headers.get("X-Admin-Token", ""), ADMIN_TOKEN)

    def do_PUT(self):
        if self.path.startswith("/api/admin/produtos/"):
            if not self._authorized(): self.send_json({"ok":False,"erro":"Não autorizado"},401); return
            code=Path(urlparse(self.path).path).name
            if not re.fullmatch(r"[A-Za-z0-9._-]+", code): self.send_error(400); return
            try:
                length=int(self.headers.get("Content-Length","0")); body=json.loads(self.rfile.read(length))
                c=db(); ensure_site_tables(c)
                p=c.execute("SELECT codigo_barras FROM produtos WHERE codigo_barras=?",(code,)).fetchone()
                if not p: c.close(); self.send_json({"ok":False,"erro":"Produto não encontrado"},404); return
                c.execute("""INSERT INTO produto_site(codigo_barras,destaque_site,destaque_regional,destaque_promocao,ordem_destaque,descricao_site,atualizado_em)
                             VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)
                             ON CONFLICT(codigo_barras) DO UPDATE SET destaque_site=excluded.destaque_site,destaque_regional=excluded.destaque_regional,
                             destaque_promocao=excluded.destaque_promocao,ordem_destaque=excluded.ordem_destaque,descricao_site=excluded.descricao_site,atualizado_em=CURRENT_TIMESTAMP""",
                          (code,int(bool(body.get("destaque_site"))),int(bool(body.get("destaque_regional"))),int(bool(body.get("destaque_promocao"))),int(body.get("ordem_destaque",9999)),str(body.get("descricao_site") or "")[:2000]))
                c.commit(); c.close(); self.send_json({"ok":True,"codigo_barras":code}); return
            except Exception as e:
                try: c.rollback(); c.close()
                except: pass
                self.send_json({"ok":False,"erro":str(e)},400); return

        if not self.path.startswith("/api/admin/imagens/"):
            self.send_error(404); return
        if not self._authorized(): self.send_json({"ok":False,"erro":"Não autorizado"},401); return
        code=Path(urlparse(self.path).path).name
        if not re.fullmatch(r"[A-Za-z0-9._-]+", code): self.send_error(400); return
        try: length=int(self.headers.get("Content-Length","0"))
        except ValueError: length=0
        if length <= 0 or length > 12*1024*1024:
            self.send_json({"ok":False,"erro":"Imagem vazia ou maior que 12 MB"},400); return
        raw=self.rfile.read(length)
        try:
            im=Image.open(BytesIO(raw)).convert("RGB")
            im.thumbnail((1200,1200), Image.Resampling.LANCZOS)
            out=BytesIO(); im.save(out,"WEBP",quality=82,method=6)
            target=IMAGE_DIR/f"{code}.webp"; target.write_bytes(out.getvalue())
        except Exception as e:
            self.send_json({"ok":False,"erro":f"Imagem inválida: {e}"},400); return
        self.send_json({"ok":True,"codigo_barras":code,"foto":f"/imagens/{code}.webp"})

    def _calcular_itens_pedido(self, c, itens):
        regras={r["nome"]:int(r["qtd_atacado"] or 0) for r in c.execute("SELECT nome,qtd_atacado FROM categorias")}
        preparados=[]; total_prod=0.0
        for item in itens:
            code=str(item.get("codigo_barras") or "").strip(); qtd=float(item.get("quantidade") or 0)
            if not code or qtd<=0: raise ValueError("Item inválido")
            p=c.execute("SELECT * FROM produtos WHERE codigo_barras=? AND status=1",(code,)).fetchone()
            if not p: raise ValueError(f"Produto não encontrado: {code}")
            stock=float(c.execute("SELECT COALESCE(SUM(quantidade),0) FROM estoque_saldos WHERE codigo_barras=?",(code,)).fetchone()[0] or 0)
            if stock < qtd: raise ValueError(f"Estoque insuficiente para {p['nome']}. Disponível: {stock:g}")
            minimo=regras.get(p["categoria"],99999) or 99999
            preco=float(p["preco_atacado"] if qtd>=minimo and p["preco_atacado"] else p["preco_varejo"])
            sub=qtd*preco; total_prod+=sub
            preparados.append({"codigo_barras":code,"nome":p["nome"],"categoria":p["categoria"],"quantidade":qtd,"preco":preco,"subtotal":sub,"estoque":stock})
        return preparados,total_prod

    def _baixar_e_registrar_venda(self,c, pedido_id, preparados, cliente, desconto, frete, endereco, bairro):
        total_prod=sum(float(x["subtotal"]) for x in preparados)
        desconto=max(0,min(float(desconto),total_prod)); total=max(0,total_prod-desconto+float(frete))
        cur=c.execute("INSERT INTO vendas (cliente,origem,desconto,total,forma_pag1,valor_pag1,forma_pag2,valor_pag2) VALUES (?,?,?,?,?,?,?,?)",
                      (cliente,"SITE",desconto,total,"PIX",total,"-",0))
        venda_id=cur.lastrowid
        for x in preparados:
            code=x["codigo_barras"]; qtd=float(x["quantidade"]); restante=qtd; afetados=[]
            locais=c.execute("SELECT local_fisico,quantidade FROM estoque_saldos WHERE codigo_barras=? AND quantidade>0 ORDER BY quantidade DESC,id",(code,)).fetchall()
            for loc in locais:
                if restante<=0: break
                tirar=min(restante,float(loc["quantidade"]))
                if tirar<=0: continue
                cur_estoque=c.execute("UPDATE estoque_saldos SET quantidade=quantidade-? WHERE codigo_barras=? AND local_fisico=? AND quantidade>=?",(tirar,code,loc["local_fisico"],tirar))
                if cur_estoque.rowcount!=1: raise ValueError(f"Estoque alterado durante o pedido: {x['nome']}")
                afetados.append(f"{tirar:g} un de {loc['local_fisico']}"); restante-=tirar
            if restante>1e-9: raise ValueError(f"Não foi possível baixar todo o estoque de {x['nome']}")
            c.execute("INSERT INTO itens_venda (venda_id,produto,preco,qtd,subtotal,local_retirada,desconto_item) VALUES (?,?,?,?,?,?,?)",
                      (venda_id,x["nome"],x["preco"],qtd,x["subtotal"],"; ".join(afetados),0))
        c.execute("UPDATE pedidos_online SET status='CONFIRMADO', atualizado_em=CURRENT_TIMESTAMP, venda_id=?, total=?, desconto=?, frete=? WHERE id=?",(venda_id,total,desconto,float(frete),pedido_id))
        return venda_id,total

    def do_POST(self):
        if self.path not in ("/api/pedidos","/api/pedidos-online"):
            self.send_error(404); return
        try:
            length=int(self.headers.get("Content-Length","0")); body=json.loads(self.rfile.read(length))
        except Exception:
            self.send_json({"ok":False,"erro":"JSON inválido"},400); return
        itens=body.get("itens") or []
        if not itens: self.send_json({"ok":False,"erro":"Pedido sem itens"},400); return
        cliente=str(body.get("cliente") or "Pedido online").strip()[:200]
        endereco=str(body.get("endereco") or "").strip()[:500]
        bairro=str(body.get("bairro") or "").strip()[:150]
        try: desconto=float(body.get("desconto") or 0); frete=float(body.get("frete") or 0)
        except Exception: self.send_json({"ok":False,"erro":"Valores inválidos"},400); return
        c=db()
        try:
            c.execute("BEGIN IMMEDIATE")
            preparados,total_prod=self._calcular_itens_pedido(c,itens)
            desconto=max(0,min(desconto,total_prod)); total=max(0,total_prod-desconto+frete)
            snapshot=json.dumps(preparados,ensure_ascii=False,separators=(",",":"))
            cur=c.execute("INSERT INTO pedidos_online (status,cliente,endereco,bairro,frete,desconto,total,forma_pagamento,itens_json,observacao) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          ("AGUARDANDO_PAGAMENTO",cliente,endereco,bairro,frete,desconto,total,"PIX",snapshot,str(body.get("observacao") or "")[:1000]))
            pedido_id=cur.lastrowid
            c.commit(); self.send_json({"ok":True,"pedido_id":pedido_id,"total":total,"status":"AGUARDANDO_PAGAMENTO","itens":preparados})
        except Exception as e:
            c.rollback(); self.send_json({"ok":False,"erro":str(e)},409)
        finally: c.close()

    def do_PUT(self):
        # Cancelamento interno de pedido ainda não faturado.
        cancel_m=re.fullmatch(r"/api/admin/pedidos/(\d+)/cancelar",urlparse(self.path).path)
        if cancel_m:
            if not self._authorized(): self.send_json({"ok":False,"erro":"Não autorizado"},401); return
            pedido_id=int(cancel_m.group(1)); c=db()
            try:
                c.execute("BEGIN IMMEDIATE")
                row=c.execute("SELECT * FROM pedidos_online WHERE id=?",(pedido_id,)).fetchone()
                if not row: raise ValueError("Pedido não encontrado")
                if row["status"]=="CONFIRMADO": raise ValueError("Pedido já confirmado e não pode ser cancelado por esta rotina")
                if row["status"]=="CANCELADO":
                    c.rollback(); self.send_json({"ok":True,"pedido_id":pedido_id,"status":"CANCELADO","ja_cancelado":True}); return
                c.execute("UPDATE pedidos_online SET status='CANCELADO', atualizado_em=CURRENT_TIMESTAMP WHERE id=?",(pedido_id,))
                c.commit(); self.send_json({"ok":True,"pedido_id":pedido_id,"status":"CANCELADO"})
            except Exception as e:
                c.rollback(); self.send_json({"ok":False,"erro":str(e)},409)
            finally: c.close()
            return

        # Confirmação interna: só equipe/admin pode transformar pedido online em venda real.
        m=re.fullmatch(r"/api/admin/pedidos/(\d+)/confirmar",urlparse(self.path).path)
        if not m: self.send_error(404); return
        if not self._authorized(): self.send_json({"ok":False,"erro":"Não autorizado"},401); return
        pedido_id=int(m.group(1)); c=db()
        try:
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("SELECT * FROM pedidos_online WHERE id=?",(pedido_id,)).fetchone()
            if not row: raise ValueError("Pedido não encontrado")
            if row["status"]=="CONFIRMADO":
                self.send_json({"ok":True,"pedido_id":pedido_id,"venda_id":row["venda_id"],"status":"CONFIRMADO","ja_confirmado":True}); c.rollback(); return
            if row["status"]!="AGUARDANDO_PAGAMENTO": raise ValueError(f"Pedido não pode ser confirmado no status {row['status']}")
            itens=json.loads(row["itens_json"])
            # Revalida estoque e preços no momento da confirmação.
            preparados,total_prod=self._calcular_itens_pedido(c,itens)
            venda_id,total=self._baixar_e_registrar_venda(c,pedido_id,preparados,row["cliente"] or "Pedido online",row["desconto"],row["frete"],row["endereco"] or "",row["bairro"] or "")
            c.commit(); self.send_json({"ok":True,"pedido_id":pedido_id,"venda_id":venda_id,"total":total,"status":"CONFIRMADO"})
        except Exception as e:
            c.rollback(); self.send_json({"ok":False,"erro":str(e)},409)
        finally: c.close()

if __name__ == "__main__":
    print(f"Loja do Vinho API em http://{HOST}:{PORT} usando {DB_PATH}")
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
