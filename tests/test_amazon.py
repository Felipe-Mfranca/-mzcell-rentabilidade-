"""
tests/test_amazon.py — Módulo Amazon contra um servidor local que imita a SP-API.

Rodar na raiz do projeto:  .\\venv\\Scripts\\python.exe -m unittest tests.test_amazon -v
Só biblioteca padrão + as dependências do requirements.txt; sem rede, sem credencial real.
"""

import gzip
import json
import os
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock
from urllib.parse import parse_qs, urlparse

from marketplaces.amazon import api, calculos, conferencia, diagnostico, relatorios

# Credenciais falsas, com o mesmo formato das reais — o servidor as devolve nos erros
# de propósito, para provar que nada disso chega a uma mensagem.
REFRESH = "Atzr|IwEBIFAKE" + "x" * 80
CLIENT_ID = "amzn1.application-oa2-client." + "ab12" * 8
SECRET = "amzn1.oa2-cs.v1." + "cd34" * 16


class FakeAmazon(BaseHTTPRequestHandler):
    estado = {}

    def log_message(self, *a):
        pass

    def _responder(self, status, corpo):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(corpo).encode())

    def do_POST(self):
        e = self.estado
        bruto = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        caminho = urlparse(self.path).path
        if caminho in e.get("posts", {}):
            corpo = json.loads(bruto or b"{}")
            e["req"].append((caminho, corpo, self.headers.get("x-amz-access-token")))
            return self._responder(*e["posts"][caminho](corpo))
        e["lwa"] += 1
        if e.get("lwa_status", 200) != 200:
            return self._responder(e["lwa_status"], {"error": "invalid_grant",
                                                     "error_description": f"{REFRESH} {CLIENT_ID} {SECRET}"})
        resp = {"access_token": f"Atza|token{e['lwa']}" + "z" * 30, "expires_in": 3600}
        if e.get("novo_refresh"):
            resp["refresh_token"] = e["novo_refresh"]
        self._responder(200, resp)

    def do_GET(self):
        u = urlparse(self.path)
        qs = {k: v[0] for k, v in parse_qs(u.query).items()}
        e = self.estado
        if u.path in e.get("arquivos", {}):          # download de documento (sem autenticação)
            dados = e["arquivos"][u.path]
            self.send_response(200)
            self.send_header("Content-Length", str(len(dados)))
            self.end_headers()
            self.wfile.write(dados)
            return
        e["req"].append((u.path, qs, self.headers.get("x-amz-access-token")))
        rota = e["rotas"].get(u.path)
        n = sum(1 for r in e["req"] if r[0] == u.path)
        status, corpo = rota(qs, n) if rota else (404, {"errors": [{"code": "NotFound"}]})
        self._responder(status, corpo)


def resumo(asin, sku, disp=0, w=0, s=0, r=0, reservado=0, nao_vendavel=0, nome="Produto"):
    return {"asin": asin, "sellerSku": sku, "fnSku": "X" + sku, "productName": nome,
            "inventoryDetails": {"fulfillableQuantity": disp, "inboundWorkingQuantity": w,
                                 "inboundShippedQuantity": s, "inboundReceivingQuantity": r,
                                 "reservedQuantity": {"totalReservedQuantity": reservado},
                                 "unfulfillableQuantity": {"totalUnfulfillableQuantity": nao_vendavel}}}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), FakeAmazon)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        FakeAmazon.estado = {"lwa": 0, "req": [], "rotas": {}}
        self.e = FakeAmazon.estado
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg_path = os.path.join(self.tmp.name, "amazon_config.json")
        self.escrever_config()
        for alvo, valor in (("CONFIG_PATH", self.cfg_path), ("AUTH_URL", self.url + "/auth/o2/token"),
                            ("ENDPOINTS", {"na": self.url})):
            p = mock.patch.object(api, alvo, valor)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch("time.sleep")
        p.start()
        self.addCleanup(p.stop)
        api._tokens.clear()
        self.addCleanup(self.tmp.cleanup)

    def escrever_config(self, **conta):
        c = {"nome": "T", "refresh_token": REFRESH, "client_id": CLIENT_ID, "client_secret": SECRET,
             "marketplace_id": "A2Q3Y263D00KWC", "region": "na", "sandbox": False,
             "asins_descontinuados": ["B0DESCONT"]}
        c.update(conta)
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump({"contas": {"amz": c}, "parametros": {"peso_7d": 0.4, "peso_30d": 0.6, "rede": ""}}, f)

    def config_em_disco(self):
        with open(self.cfg_path, encoding="utf-8") as f:
            return f.read()


class TestCalculos(unittest.TestCase):
    def test_estoque_valido_soma_transito_e_ignora_reservado_e_nao_vendavel(self):
        r = calculos.linha_de_estoque(resumo("B1", "S1", disp=10, w=3, s=5, r=2, reservado=99, nao_vendavel=99))
        self.assertEqual((r["disponivel"], r["em_transito"], r["estoque_valido"]), (10, 10, 20))

    def test_mesmo_asin_com_dois_skus_vira_uma_linha(self):
        a = calculos.linha_de_estoque(resumo("B0GXX6NG28", "KIT3TAP-FBA", disp=10, w=5))
        b = calculos.linha_de_estoque(resumo("B0GXX6NG28", "KIT3-TAPMAG-SORTIDO", disp=4, s=1, nome=""))
        g = calculos.agrupar_estoque_por_asin([a, b])
        self.assertEqual(len(g), 1)
        self.assertEqual((g[0]["disponivel"], g[0]["em_transito"], g[0]["estoque_valido"]), (14, 6, 20))
        self.assertEqual(g[0]["sku"], "KIT3-TAPMAG-SORTIDO + KIT3TAP-FBA")

    def test_titulo_vazio_herda_do_proximo_sku(self):
        a = calculos.linha_de_estoque(resumo("B1", "A", nome=""))
        b = calculos.linha_de_estoque(resumo("B1", "B", nome="Kit Tapete"))
        self.assertEqual(calculos.agrupar_estoque_por_asin([a, b])[0]["titulo"], "Kit Tapete")

    def _buckets(self, hoje, dias, und, omitir=()):
        return [{"interval": f"{hoje - timedelta(days=i)}T00:00:00-07:00--{hoje - timedelta(days=i - 1)}T00:00:00-07:00",
                 "unitCount": und(i)} for i in range(dias, 0, -1) if i not in omitir]

    def test_velocidade_completa(self):
        hoje = date(2026, 10, 14)
        v = calculos.velocidade(self._buckets(hoje, 30, lambda i: 1), hoje)
        self.assertEqual((v["vendido_7d"], v["vendido_30d"]), (7, 30))
        self.assertAlmostEqual(v["velocidade"], 1.0, places=3)  # 1/dia nas duas janelas

    def test_janela_de_7d_pela_data_e_nao_pela_posicao(self):
        """Dias sem venda omitidos da resposta não podem esticar a janela de 7 dias."""
        hoje = date(2026, 10, 14)
        # só 3 dias com venda, todos antigos (dias 20, 15 e 10 atrás) e 1 recente (dia 2)
        buckets = [b for b in self._buckets(hoje, 30, lambda i: 5) if
                   int(b["interval"].split("--")[0][8:10]) in {(hoje - timedelta(days=d)).day for d in (20, 15, 10, 2)}]
        v = calculos.velocidade(buckets, hoje)
        self.assertEqual(v["vendido_7d"], 5)    # só o do dia 2; por posição seriam 20
        self.assertEqual(v["vendido_30d"], 20)

    def test_pesos(self):
        hoje = date(2026, 10, 14)
        b = self._buckets(hoje, 30, lambda i: 7 if i <= 7 else 0)   # 49 nos últimos 7d, nada antes
        v = calculos.velocidade(b, hoje, 0.4, 0.6)
        self.assertEqual(v["velocidade_7d"], 7.0)
        self.assertAlmostEqual(v["velocidade"], 7.0 * 0.4 + (49 / 30) * 0.6, places=3)


class TestApi(Base):
    def test_paginacao_descontinuados_e_consolidacao(self):
        def inv(qs, n):
            if "nextToken" not in qs:
                return 200, {"payload": {"inventorySummaries": [resumo("B1", "A", disp=5), resumo("B0DESCONT", "X", disp=9)],
                                         "pagination": {"nextToken": "p2"}}}
            return 200, {"payload": {"inventorySummaries": [resumo("B1", "B", disp=2, w=1)]}}
        self.e["rotas"]["/fba/inventory/v1/summaries"] = inv
        itens = api.estoque_fba("amz")
        self.assertEqual([(i["asin"], i["estoque_valido"]) for i in itens], [("B1", 8)])

    def test_falha_na_segunda_pagina_nao_devolve_lista_cortada(self):
        def inv(qs, n):
            if "nextToken" not in qs:
                return 200, {"payload": {"inventorySummaries": [resumo("B1", "A", disp=5)],
                                         "pagination": {"nextToken": "p2"}}}
            return 500, {"errors": ["boom"]}
        self.e["rotas"]["/fba/inventory/v1/summaries"] = inv
        with self.assertRaises(api.ErroAmazon):
            api.estoque_fba("amz")

    def test_403_explica_que_falta_role(self):
        self.e["rotas"]["/fba/inventory/v1/summaries"] = lambda qs, n: (403, {"errors": [{"code": "Unauthorized"}]})
        with self.assertRaises(api.ErroAmazon) as ctx:
            api.estoque_fba("amz")
        self.assertEqual(ctx.exception.status, 403)
        self.assertIn("role", str(ctx.exception))

    def test_401_renova_o_token_uma_vez_e_repete(self):
        self.e["rotas"]["/sellers/v1/marketplaceParticipations"] = \
            lambda qs, n: (401, {}) if n == 1 else (200, {"payload": []})
        status, _ = api.get("amz", "/sellers/v1/marketplaceParticipations")
        self.assertEqual(status, 200)
        self.assertEqual(self.e["lwa"], 2)   # o inicial + a renovação

    def test_token_fica_em_cache_e_nunca_vai_para_o_disco(self):
        self.e["rotas"]["/x"] = lambda qs, n: (200, {})
        for _ in range(3):
            api.get("amz", "/x")
        self.assertEqual(self.e["lwa"], 1)
        self.assertNotIn("Atza|", self.config_em_disco())

    def test_refresh_token_novo_e_gravado(self):
        self.e["novo_refresh"] = "Atzr|NOVO" + "n" * 40
        self.e["rotas"]["/x"] = lambda qs, n: (200, {})
        api.get("amz", "/x")
        self.assertIn("Atzr|NOVO", self.config_em_disco())

    def test_credencial_recusada_nao_vaza_em_nenhuma_mensagem(self):
        self.e["lwa_status"] = 400
        with self.assertRaises(api.ErroAmazon) as ctx:
            api.access_token("amz")
        msg = str(ctx.exception)
        self.assertEqual(ctx.exception.status, 401)
        for segredo in (REFRESH, CLIENT_ID, SECRET, "ab12ab12ab12", "cd34cd34cd34"):
            self.assertNotIn(segredo, msg)
        self.assertIn("OCULTO", msg)

    def test_motivo_do_lwa_aparece_mesmo_com_error_index_gigante(self):
        """O LWA manda um error_index de centenas de caracteres antes do motivo."""
        e = self.e
        e["lwa_status"] = 400
        original = FakeAmazon.do_POST

        def post(handler):
            e["lwa"] += 1
            handler.rfile.read(int(handler.headers.get("Content-Length", 0)))
            handler._responder(400, {"error_index": "T7eV" + "q" * 400,
                                     "error": "invalid_grant",
                                     "error_description": "The request has an invalid grant parameter"})
        with mock.patch.object(FakeAmazon, "do_POST", post):
            with self.assertRaises(api.ErroAmazon) as ctx:
                api.access_token("amz")
        msg = str(ctx.exception)
        self.assertIn("invalid_grant", msg)
        self.assertIn("invalid grant parameter", msg)
        self.assertNotIn("qqqq", msg)
        self.assertLess(len(msg), 250)

    def test_erro_da_api_que_ecoa_credencial_tambem_e_mascarado(self):
        self.e["rotas"]["/fba/inventory/v1/summaries"] = \
            lambda qs, n: (400, {"echo": f"{REFRESH} {CLIENT_ID} {SECRET}"})
        with self.assertRaises(api.ErroAmazon) as ctx:
            api.estoque_fba("amz")
        for segredo in (REFRESH, CLIENT_ID, SECRET):
            self.assertNotIn(segredo, str(ctx.exception))

    def test_vendas_soma_todas_as_logisticas_por_padrao(self):
        self.e["rotas"]["/sales/v1/orderMetrics"] = lambda qs, n: (200, {"payload": []})
        api.vendas_asin("amz", "B1")
        qs = self.e["req"][-1][1]
        self.assertEqual(qs["asin"], "B1")
        self.assertNotIn("fulfillmentNetwork", qs)
        api.vendas_asin("amz", "B1", rede="afn")
        self.assertEqual(self.e["req"][-1][1]["fulfillmentNetwork"], "AFN")

    def test_dia_fechado_e_o_dia_de_brasilia_nao_o_de_utc(self):
        """O Seller Central fecha o dia à meia-noite de Brasília (UTC-3)."""
        self.e["rotas"]["/sales/v1/orderMetrics"] = lambda qs, n: (200, {"payload": []})
        api.vendas_asin("amz", "B1")
        qs = self.e["req"][-1][1]
        ini, fim = qs["interval"].split("--")
        self.assertTrue(ini.endswith("T00:00:00-03:00"), ini)
        self.assertTrue(fim.endswith("T00:00:00-03:00"), fim)
        self.assertEqual(qs["granularityTimeZone"], "America/Sao_Paulo")
        d_ini, d_fim = date.fromisoformat(ini[:10]), date.fromisoformat(fim[:10])
        self.assertEqual((d_fim - d_ini).days, 30)

    def test_fuso_utc_continua_disponivel_como_saida(self):
        with open(self.cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["parametros"]["fuso"] = "UTC"
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        self.e["rotas"]["/sales/v1/orderMetrics"] = lambda qs, n: (200, {"payload": []})
        api.vendas_asin("amz", "B1")
        qs = self.e["req"][-1][1]
        self.assertTrue(qs["interval"].split("--")[0].endswith("T00:00:00+00:00"))
        self.assertEqual(qs["granularityTimeZone"], "UTC")

    def test_fuso_desconhecido_e_rejeitado(self):
        with open(self.cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["parametros"]["fuso"] = "Marte/Olympus"
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        with self.assertRaises(api.ErroAmazon) as ctx:
            api.vendas_asin("amz", "B1")
        self.assertEqual(ctx.exception.status, 400)

    def test_rede_invalida_e_rejeitada(self):
        with self.assertRaises(api.ErroAmazon) as ctx:
            api.vendas_asin("amz", "B1", rede="XYZ")
        self.assertEqual(ctx.exception.status, 400)

    def test_autenticado_ignora_placeholder(self):
        self.assertTrue(api.autenticado("amz"))
        self.escrever_config(refresh_token="COLE_AQUI")
        self.assertFalse(api.autenticado("amz"))
        self.assertFalse(api.autenticado("nao_existe"))

    def test_conta_inexistente_e_404(self):
        with self.assertRaises(api.ErroAmazon) as ctx:
            api.conta_cfg("nao_existe")
        self.assertEqual(ctx.exception.status, 404)

    def test_sem_arquivo_de_config_orienta_o_usuario(self):
        os.remove(self.cfg_path)
        with self.assertRaises(api.ErroAmazon) as ctx:
            api.contas()
        self.assertIn("amazon_config.exemplo.json", str(ctx.exception))

    def test_sandbox_troca_o_host(self):
        self.assertEqual(api.base_url("amz"), self.url)
        self.escrever_config(sandbox=True)
        self.assertEqual(api.base_url("amz"), api.ENDPOINTS_SANDBOX["na"])


CABECALHO = "amazon-order-id\tpurchase-date\torder-status\tfulfillment-channel\tsku\tasin\tquantity\titem-status"


def tsv(*linhas):
    return "\n".join([CABECALHO] + ["\t".join(map(str, l)) for l in linhas]) + "\n"


# janela de teste: 14/09 a 13/10 em Brasília = 14/09 03:00Z até 14/10 03:00Z (exclusivo)
PEDIDOS = [
    ("A1", "2026-09-14T02:59:00+00:00", "Shipped",   "Amazon",   "SKU1", "B1", 4, "Shipped"),    # 13/09 23:59 BRT → FORA
    ("A2", "2026-09-14T03:00:00+00:00", "Shipped",   "Amazon",   "SKU1", "B1", 2, "Shipped"),    # 14/09 00:00 BRT → dentro
    ("A3", "2026-10-14T02:59:59+00:00", "Shipped",   "Amazon",   "SKU1", "B1", 3, "Shipped"),    # 13/10 23:59 BRT → dentro
    ("A4", "2026-10-14T03:00:00+00:00", "Pending",   "Amazon",   "SKU1", "B1", 7, "Unshipped"),  # 14/10 00:00 BRT → FORA
    ("A5", "2026-09-20T15:00:00+00:00", "Cancelled", "Amazon",   "SKU1", "B1", 5, "Cancelled"),  # dentro, cancelado
    ("A6", "2026-09-21T15:00:00+00:00", "Shipped",   "Amazon",   "OUTRO", "B2", 9, "Shipped"),   # outro ASIN
    ("A7", "2026-09-22T15:00:00+00:00", "Shipped",   "Merchant", "SKU2", "B1", 1, "Shipped"),    # DBA do mesmo ASIN
]
BRT = timezone(timedelta(hours=-3))


class TestConciliacao(unittest.TestCase):
    def linhas(self):
        return relatorios.ler_tsv(tsv(*PEDIDOS))

    def test_borda_da_janela_em_brasilia(self):
        r = calculos.conciliar_pedidos(self.linhas(), "B1", date(2026, 9, 14), date(2026, 10, 13), BRT)
        # dentro: A2(2) + A3(3) + A5(5, cancelado) + A7(1); fora: A1 (23:59 do dia 13/09), A4 (00:00 do dia 14/10)
        self.assertEqual((r["unidades_total"], r["unidades_canceladas"], r["unidades_liquidas"]), (11, 5, 6))
        self.assertEqual(r["pedidos"], 4)

    def test_o_mesmo_dado_em_utc_da_outro_total(self):
        """É exatamente a divergência de fuso: A1 entra e A3 sai quando o dia é UTC."""
        r = calculos.conciliar_pedidos(self.linhas(), "B1", date(2026, 9, 14), date(2026, 10, 13), timezone.utc)
        self.assertEqual(r["unidades_total"], 11 - 3 + 4)

    def test_soma_por_asin_com_todos_os_skus_e_logisticas(self):
        r = calculos.conciliar_pedidos(self.linhas(), "b1", date(2026, 9, 14), date(2026, 10, 13), BRT)  # caixa ignorada
        self.assertEqual(r["por_sku"], {("SKU1", "Amazon"): 10, ("SKU2", "Merchant"): 1})
        self.assertNotIn("OUTRO", {s for s, _ in r["por_sku"]})

    def test_por_dia_usa_o_dia_de_brasilia(self):
        r = calculos.conciliar_pedidos(self.linhas(), "B1", date(2026, 9, 14), date(2026, 10, 13), BRT)
        self.assertEqual(r["por_dia"][date(2026, 10, 13)]["total"], 3)       # A3: 23:59 BRT, ainda dia 13
        self.assertEqual(r["por_dia"][date(2026, 9, 20)], {"total": 5, "canceladas": 5})

    def test_relatorio_sem_colunas_esperadas_explica(self):
        with self.assertRaises(ValueError) as ctx:
            calculos.conciliar_pedidos([{"foo": "1"}], "B1", date(2026, 9, 14), date(2026, 10, 13), BRT)
        self.assertIn("foo", str(ctx.exception))

    def test_relatorio_vazio_soma_zero(self):
        r = calculos.conciliar_pedidos([], "B1", date(2026, 9, 14), date(2026, 10, 13), BRT)
        self.assertEqual((r["unidades_total"], r["pedidos"]), (0, 0))


JANELA_PADRAO_REAL = conferencia.janela_padrao


class TestConferencia(Base):
    def setUp(self):
        super().setUp()
        # Fixa a janela padrão longe das datas dos testes: sem isso, nos dias em que a data real
        # coincide com a janela escolhida aqui, o comando tentaria comparar com o orderMetrics.
        p = mock.patch.object(conferencia, "janela_padrao", lambda fuso: (date(2000, 1, 1), date(2000, 1, 30)))
        p.start()
        self.addCleanup(p.stop)

    def montar_relatorio(self, conteudo, status_final="DONE", gz=True):
        e = self.e
        e["posts"] = {relatorios.REPORTS_PATH: lambda corpo: (202, {"reportId": "R1"})}
        e["rotas"][f"{relatorios.REPORTS_PATH}/R1"] = lambda qs, n: (
            (200, {"processingStatus": "IN_PROGRESS"}) if n == 1 else
            (200, {"processingStatus": status_final, "reportDocumentId": "D1"}))
        e["rotas"][f"{relatorios.DOCUMENTS_PATH}/D1"] = lambda qs, n: (
            200, {"reportDocumentId": "D1", "url": self.url + "/doc", **({"compressionAlgorithm": "GZIP"} if gz else {})})
        dados = conteudo.encode("utf-8")
        e["arquivos"] = {"/doc": gzip.compress(dados) if gz else dados}

    def test_fluxo_completo_e_janela_enviada_em_utc(self):
        self.montar_relatorio(tsv(*PEDIDOS))
        r = conferencia.conferir("amz", "B1", date(2026, 9, 14), date(2026, 10, 13))
        criar = next(x for x in self.e["req"] if x[0] == relatorios.REPORTS_PATH)[1]
        self.assertEqual(criar["reportType"], conferencia.TIPO_PEDIDOS)
        self.assertEqual(criar["dataStartTime"], "2026-09-14T03:00:00Z")    # 00:00 BRT
        self.assertEqual(criar["dataEndTime"], "2026-10-14T03:00:00Z")      # 00:00 BRT do dia seguinte
        self.assertEqual(criar["marketplaceIds"], ["A2Q3Y263D00KWC"])
        self.assertEqual((r["unidades_total"], r["unidades_liquidas"]), (11, 6))
        self.assertIsNone(r["ordermetrics"])     # janela personalizada: não comparável
        self.assertEqual(r["noite"], 3)          # A3, comprado às 23:59 de Brasília

    def test_documento_sem_gzip_e_em_cp1252(self):
        texto = tsv(("A9", "2026-09-20T15:00:00+00:00", "Shipped", "Amazon", "SKUÃ", "B1", 2, "Shipped"))
        self.e["posts"] = {relatorios.REPORTS_PATH: lambda c: (202, {"reportId": "R1"})}
        self.e["rotas"][f"{relatorios.REPORTS_PATH}/R1"] = lambda qs, n: (200, {"processingStatus": "DONE", "reportDocumentId": "D1"})
        self.e["rotas"][f"{relatorios.DOCUMENTS_PATH}/D1"] = lambda qs, n: (200, {"url": self.url + "/doc"})
        self.e["arquivos"] = {"/doc": texto.encode("cp1252")}
        r = conferencia.conferir("amz", "B1", date(2026, 9, 14), date(2026, 10, 13))
        self.assertEqual(r["por_sku"], {("SKUÃ", "Amazon"): 2})

    def test_janela_padrao_compara_com_ordermetrics(self):
        fuso = api.FUSOS["America/Sao_Paulo"]
        de, ate = JANELA_PADRAO_REAL(fuso)
        self.assertEqual((ate - de).days, 29)                                  # 30 dias fechados
        self.assertEqual(ate, datetime.now(fuso).date() - timedelta(days=1))   # termina ontem
        quando = f"{ate}T15:00:00+00:00"
        self.montar_relatorio(tsv(("A1", quando, "Shipped", "Amazon", "SKU1", "B1", 5, "Shipped")))
        self.e["rotas"]["/sales/v1/orderMetrics"] = lambda qs, n: (200, {"payload": [
            {"interval": f"{ate}T00:00:00-03:00--{ate + timedelta(days=1)}T00:00:00-03:00", "unitCount": 5}]})
        with mock.patch.object(conferencia, "janela_padrao", JANELA_PADRAO_REAL):
            r = conferencia.conferir("amz", "B1")
        self.assertEqual((r["unidades_total"], r["ordermetrics"]), (5, 5))

    def test_falha_no_ordermetrics_nao_derruba_o_resultado_do_relatorio(self):
        ontem = JANELA_PADRAO_REAL(api.FUSOS["America/Sao_Paulo"])[1]
        self.montar_relatorio(tsv(("A1", f"{ontem}T15:00:00+00:00", "Shipped", "Amazon", "SKU1", "B1", 5, "Shipped")))
        self.e["rotas"]["/sales/v1/orderMetrics"] = lambda qs, n: (403, {})
        with mock.patch.object(conferencia, "janela_padrao", JANELA_PADRAO_REAL):
            r = conferencia.conferir("amz", "B1")
        self.assertEqual(r["unidades_total"], 5)
        self.assertIsNone(r["ordermetrics"])
        self.assertIn("role", r["ordermetrics_erro"])
        self.assertIn("Não consegui consultar o orderMetrics", conferencia.formatar(r))

    def test_relatorio_fatal_explica(self):
        self.montar_relatorio("", status_final="FATAL")
        with self.assertRaises(api.ErroAmazon) as ctx:
            conferencia.conferir("amz", "B1", date(2026, 9, 14), date(2026, 10, 13))
        self.assertIn("FATAL", str(ctx.exception))

    def test_403_na_criacao_diz_que_falta_role(self):
        self.e["posts"] = {relatorios.REPORTS_PATH: lambda c: (403, {"errors": [{"code": "Unauthorized"}]})}
        with self.assertRaises(api.ErroAmazon) as ctx:
            conferencia.conferir("amz", "B1", date(2026, 9, 14), date(2026, 10, 13))
        self.assertEqual(ctx.exception.status, 403)

    def test_janela_acima_de_30_dias_e_recusada_antes_de_chamar_a_amazon(self):
        with self.assertRaises(api.ErroAmazon) as ctx:
            conferencia.conferir("amz", "B1", date(2026, 9, 1), date(2026, 10, 13))
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(self.e["req"], [])

    def test_saida_mostra_as_tres_leituras_e_o_veredito(self):
        self.montar_relatorio(tsv(*PEDIDOS))
        r = conferencia.conferir("amz", "B1", date(2026, 9, 14), date(2026, 10, 13))
        r["ordermetrics"] = 6                       # simula o orderMetrics batendo com as líquidas
        texto = conferencia.formatar(r, sc=11)
        self.assertIn("Seller Central (informado em --sc)", texto)
        self.assertIn("Seller Central (11) bate exatamente com todas as unidades", texto)
        self.assertIn("orderMetrics (6) bate exatamente com as líquidas", texto)
        r["ordermetrics"] = 8
        self.assertIn("não bate com nenhuma", conferencia.formatar(r))

    def test_csv_tem_pedido_a_pedido_e_nenhum_dado_do_comprador(self):
        self.montar_relatorio(tsv(*PEDIDOS))
        r = conferencia.conferir("amz", "B1", date(2026, 9, 14), date(2026, 10, 13))
        antes = os.getcwd()
        os.chdir(self.tmp.name)
        try:
            caminho = conferencia.salvar_csv(r)
            with open(caminho, encoding="utf-8-sig") as f:
                linhas = f.read().splitlines()
        finally:
            os.chdir(antes)
        self.assertEqual(linhas[0], "pedido;data_hora_local;status;sku;canal;quantidade;cancelada")
        self.assertEqual(len(linhas), 1 + 4)
        self.assertIn("A3;13/10/2026 23:59:59;Shipped;SKU1;Amazon;3;não", linhas)


class TestDiagnostico(Base):
    def test_classificacao_e_mapa(self):
        tabela = {"/sellers/v1/marketplaceParticipations": 200, "/fba/inventory/v1/summaries": 200,
                  "/orders/v0/orders": 403, "/catalog/2022-04-01/items": 400, "/products/pricing/v0/price": 404,
                  "/finances/v0/financialEvents": 403, "/mfn/v0/sellerInputs": 429}
        for caminho, st in tabela.items():
            self.e["rotas"][caminho] = (lambda s: lambda qs, n: (s, {"eco": f"{REFRESH} {SECRET}"}))(st)
        r = diagnostico.sondar("amz", pausa=0)
        por_modulo = {m["modulo"]: m["situacao"] for m in r["modulos"]}
        self.assertEqual(por_modulo["Pedidos"], "SEM ROLE")
        self.assertEqual(por_modulo["Financeiro"], "SEM ROLE")
        self.assertEqual(por_modulo["Catálogo (leitura)"], "LIBERADO")   # 400
        self.assertEqual(por_modulo["Preço"], "LIBERADO")                # 404
        self.assertEqual(por_modulo["Expedição ao cliente"], "LIBERADO") # 429
        self.assertEqual(r["total"], 10)
        saida = json.dumps(r, ensure_ascii=False) + diagnostico.formatar(r)
        for segredo in (REFRESH, SECRET):
            self.assertNotIn(segredo, saida)

    def test_cli_distingue_rede_de_credencial(self):
        self.e["lwa_status"] = 400
        with mock.patch("builtins.print"):
            self.assertEqual(diagnostico.main(["amz"]), 1)          # a Amazon recusou
        with mock.patch.object(api, "AUTH_URL", "http://127.0.0.1:9/x"), mock.patch("builtins.print"):
            self.assertEqual(diagnostico.main(["amz"]), 2)          # nem chegou na Amazon


class TestRouter(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from marketplaces.amazon.router import router
        app = FastAPI()
        app.include_router(router)
        cls.client = TestClient(app)

    def test_contas_nao_expoe_credencial(self):
        r = self.client.get("/amazon/contas")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [{"conta": "amz", "autenticado": True, "sandbox": False}])
        for segredo in (REFRESH, SECRET, CLIENT_ID):
            self.assertNotIn(segredo, r.text)

    def test_estoque_ok(self):
        self.e["rotas"]["/fba/inventory/v1/summaries"] = \
            lambda qs, n: (200, {"payload": {"inventorySummaries": [resumo("B1", "A", disp=3)]}})
        j = self.client.get("/amazon/estoque/amz").json()
        self.assertEqual((j["total_asins"], j["itens"][0]["estoque_valido"]), (1, 3))

    def test_403_da_amazon_vira_403_com_explicacao(self):
        self.e["rotas"]["/fba/inventory/v1/summaries"] = lambda qs, n: (403, {})
        r = self.client.get("/amazon/estoque/amz")
        self.assertEqual(r.status_code, 403)
        self.assertIn("role", r.json()["detail"])

    def test_conta_inexistente_404_e_vendas_exige_asin(self):
        self.assertEqual(self.client.get("/amazon/estoque/nada").status_code, 404)
        self.assertEqual(self.client.get("/amazon/vendas/amz").status_code, 422)


class TestIntegracaoNoMain(unittest.TestCase):
    def test_main_monta_as_rotas_amazon(self):
        try:
            import main
        except Exception as e:           # dependência ausente no ambiente de teste
            self.skipTest(f"main.py não importou aqui: {e}")
        rotas = set(main.app.openapi()["paths"])   # não depende de como o FastAPI guarda os routers
        for esperada in ("/amazon/contas", "/amazon/diagnostico/{conta}",
                         "/amazon/estoque/{conta}", "/amazon/vendas/{conta}"):
            self.assertIn(esperada, rotas)


class TestContrato(unittest.TestCase):
    def test_amazon_cumpre_o_contrato_de_marketplace(self):
        from marketplaces.amazon.marketplace import AmazonMarketplace
        m = AmazonMarketplace()
        self.assertEqual(m.nome, "amazon")
        with self.assertRaises(NotImplementedError):
            m.listar_anuncios("amz")
        with self.assertRaises(NotImplementedError):
            m.listar_pedidos("amz", "2026-01-01", "2026-01-31")


if __name__ == "__main__":
    unittest.main()
