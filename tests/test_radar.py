"""Testes do Radar ML: regras de posição, armazenamento, fila e orquestração do robô. Sem rede e sem navegador."""
import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from marketplaces.radar import parse, robo, store

M1, M2, M3 = "MLB1000000001", "MLB1000000002", "MLB1000000003"


def link(mlb, position, tipo="item", extra=""):
    return (f"https://produto.mercadolivre.com.br/{mlb}-produto#position={position}&search_layout=grid"
            f"&type={tipo}&tracking_id=abc{extra}&wid={mlb}&sid=search")


def card(mlb, position, tipo="item"):
    return [link(mlb, position, tipo), "https://www.mercadolivre.com.br/perfil/loja"]


def pagina_de(*mlbs, inicio=1):
    return [card(m, inicio + i) for i, m in enumerate(mlbs)]


class TestParse(unittest.TestCase):
    def test_normalizacao(self):
        self.assertEqual(parse.normalizar_keyword("  Escova   DE  Canto "), "escova de canto")
        self.assertEqual(parse.normalizar_mlb("mlb-5352112284"), "MLB5352112284")
        self.assertEqual(parse.normalizar_mlb("5352112284"), "MLB5352112284")
        self.assertEqual(parse.normalizar_mlb("abc"), "")

    def test_urls(self):
        self.assertEqual(parse.url_busca("Escova de Canção"),
                         "https://lista.mercadolivre.com.br/escova-de-cancao?sb=all_mercadolibre")
        self.assertEqual(parse.url_busca("jogo de facas", 3),
                         "https://lista.mercadolivre.com.br/jogo-de-facas_Desde_101_NoIndex_True?sb=all_mercadolibre")
        self.assertNotIn("/e/negocios", parse.url_busca("x", 2))     # nunca a vitrine de Negócios

    def test_link_le_fragmento_e_query(self):
        lk = parse.parse_link("https://x.com/p?wid=MLB1000000001&position=4&type=pad")
        self.assertEqual((lk["wid"], lk["position"], lk["type"]), (M1, 4, "pad"))
        self.assertIsNone(parse.parse_link("https://www.mercadolivre.com.br/perfil/loja"))

    def test_posicao_geral_50_organicos_por_pagina(self):
        self.assertEqual(parse.posicao_geral(10, 2), 60)      # 10º orgânico da página 2 = #60
        self.assertEqual(parse.posicao_geral(7, 3), 107)
        self.assertEqual(parse.posicao_geral(7, 1), 7)

    def test_resolver_ignora_ads_na_contagem(self):
        cards = [card(M2, 1), card(M1, 2, "pad"), card(M3, 3), card(M1, 4, "item")]
        r = parse.resolver(cards, M1, 1)
        self.assertEqual((r["posicao"], r["pagina_pos"], r["ad_type"]), (3, 3, "organico"))   # o ADS não ocupa posição
        self.assertIsNone(parse.resolver([card(M1, 2, "pad")], M1, 1))     # só como ADS: não conta
        self.assertIsNone(parse.resolver(cards, "MLB9999999999", 1))      # nunca inventa

    def test_exemplo_pagina_02_posicao_60(self):
        """Página 2 com ADS intercalados: o anúncio é o 10º orgânico -> Página 02 · posição #60."""
        outros = [f"MLB20000000{i:02d}" for i in range(1, 10)]
        cards = []
        for i, m in enumerate(outros):
            cards.append(card(m, i + 1))
            if i % 3 == 0:
                cards.append(card(f"MLB30000000{i:02d}", 99, "pad"))      # ADS no meio da página
        cards.append(card(M1, 999))                                      # contador do ML é ignorado
        r = parse.resolver(cards, M1, 2)
        self.assertEqual((r["pagina"], r["posicao"], r["pagina_pos"]), (2, 60, 10))

    def test_resolver_pagina_pos_independe_do_contador(self):
        cards = [card(M2, 1), card(M3, 2), card(M1, 57)]    # contador do ML passou de 50 numa página
        r = parse.resolver(cards, M1, 1)
        self.assertEqual((r["posicao"], r["pagina_pos"]), (3, 3))

    def test_url_sem_rastreio(self):
        r = parse.resolver([card(M1, 1)], M1, 1)
        self.assertNotIn("tracking_id", r["url"])
        self.assertNotIn("#", r["url"])

    def test_achar_paginas(self):
        self.assertEqual(parse.achar_paginas({2: [M2], 3: [M1, M2], 4: [M1]}, [M1, M2, M3]), {M2: 2, M1: 3})

    def test_variacao(self):
        v = parse.variacao
        self.assertEqual([v(3, 5), v(5, 3), v(4, 4), v(None, 4), v(4, None), v(None, None)],
                         ["subiu", "caiu", "igual", "fora", "novo", ""])

    def test_bloqueio_e_viewer(self):
        self.assertEqual(parse.detectar_bloqueio("Por segurança, complete esta etapa"), "captcha")
        self.assertEqual(parse.detectar_bloqueio("ok", "https://www.mercadolivre.com/jms/mlb/lgz/login"), "login")
        self.assertEqual(parse.detectar_bloqueio("resultados da busca", "https://lista.mercadolivre.com.br/x"), "")
        self.assertEqual(parse.extrair_viewer("Início\nContato\nSM ONLINE\nCompras\nVender"), "SM ONLINE")


class NavFalso:
    """Busca falsa: {keyword: {pagina: cards}}. `falha_descoberta` = páginas em que o fetch em lote 'falha'."""

    def __init__(self, paginas, viewer="SM ONLINE", falha_descoberta=(), bloqueia_em=None):
        self.paginas, self._viewer, self.falha, self.bloqueia_em = paginas, viewer, set(falha_descoberta), bloqueia_em
        self.abertas, self.lotes, self.fechou = [], [], False

    def viewer(self):
        return self._viewer

    def cards(self, keyword, pagina):
        if self.bloqueia_em == (keyword, pagina):
            raise robo.Bloqueio("captcha")
        self.abertas.append((keyword, pagina))
        return self.paginas.get(keyword, {}).get(pagina, [])

    def wids_paginas(self, keyword, paginas):
        self.lotes.append((keyword, list(paginas)))
        out = {}
        for p in paginas:
            if p in self.falha:
                out[p] = None
            else:
                out[p] = sorted({parse.parse_link(h)["wid"] for c in self.paginas.get(keyword, {}).get(p, [])
                                 for h in c if parse.parse_link(h)})
        return out

    def fechar(self):
        self.fechou = True


def busca(**por_pagina):
    return {int(k[1:]): v for k, v in por_pagina.items()}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        for alvo, nome in ((store, "DADOS_PATH"), (robo, "CONFIG_PATH")):
            p = mock.patch.object(alvo, nome, os.path.join(self.dir.name, nome.lower() + ".json"))
            p.start(); self.addCleanup(p.stop)
        pub = mock.patch.object(store, "_publicar"); self.pub = pub.start(); self.addCleanup(pub.stop)
        # nada de teste chega à planilha real (remover_watch fala direto com o shared_store)
        import shared_store
        for nome in ("enviar", "enviar_async", "enviar_lote", "enviar_lote_async"):
            p = mock.patch.object(shared_store, nome); p.start(); self.addCleanup(p.stop)


class TestVerificarKeyword(Base):
    def test_acha_na_pagina_1(self):
        nav = NavFalso({"k": busca(p1=pagina_de(M2, M1))})
        r = robo.verificar_keyword(nav, "k", [M1])
        self.assertEqual((r[M1]["encontrado"], r[M1]["pagina"], r[M1]["posicao"], r[M1]["pagina_pos"]), (True, 1, 2, 2))
        self.assertEqual(nav.lotes, [])               # achou na 1: nem baixa as outras

    def test_acha_na_pagina_3_e_abre_so_ela(self):
        nav = NavFalso({"k": busca(p1=pagina_de(M2), p2=pagina_de(M3, inicio=1), p3=pagina_de(M2, M1, inicio=6))})
        r = robo.verificar_keyword(nav, "k", [M1])
        self.assertEqual((r[M1]["pagina"], r[M1]["posicao"], r[M1]["pagina_pos"]), (3, 102, 2))
        self.assertEqual(nav.abertas, [("k", 1), ("k", 3)])    # a 2 só foi baixada, não aberta

    def test_pagina_1_vazia_e_bloqueio_nao_nao_encontrado(self):
        nav = NavFalso({"k": busca(p1=[])})
        with self.assertRaises(robo.Bloqueio):
            robo.verificar_keyword(nav, "k", [M1])

    def test_ads_numa_pagina_organico_na_seguinte(self):
        nav = NavFalso({"k": busca(p1=pagina_de(M2), p2=[card(M3, 1), card(M1, 2, "pad")], p3=pagina_de(M2, M3, M1))})
        r = robo.verificar_keyword(nav, "k", [M1])
        self.assertEqual((r[M1]["pagina"], r[M1]["posicao"]), (3, 103))       # o ADS da página 2 não conta
        self.assertEqual(nav.abertas, [("k", 1), ("k", 2), ("k", 3)])

    def test_nao_achado_percorre_20_paginas_e_nao_inventa(self):
        paginas = {p: pagina_de(M2, inicio=1) for p in range(1, 21)}
        nav = NavFalso({"k": paginas})
        r = robo.verificar_keyword(nav, "k", [M1], 20)
        self.assertEqual(r[M1], {"encontrado": False, "max_pagina": 20})
        self.assertEqual(nav.abertas, [("k", 1)])
        self.assertEqual(sum(len(p) for _, p in nav.lotes), 19)   # 2..20, sempre, sem olhar o "total"

    def test_pagina_vazia_confirmada_encerra(self):
        nav = NavFalso({"k": busca(p1=pagina_de(M2), p2=pagina_de(M3), p3=[])})
        r = robo.verificar_keyword(nav, "k", [M1], 20)
        self.assertEqual(r[M1], {"encontrado": False, "max_pagina": 2})
        self.assertIn(("k", 3), nav.abertas)                   # conferiu abrindo a página vazia

    def test_falha_na_descoberta_abre_a_pagina(self):
        nav = NavFalso({"k": busca(p1=pagina_de(M2), p2=pagina_de(M1, inicio=60))}, falha_descoberta=[2])
        r = robo.verificar_keyword(nav, "k", [M1], 20)
        self.assertEqual((r[M1]["pagina"], r[M1]["posicao"]), (2, 51))       # 1º orgânico da página 2

    def test_dois_anuncios_um_termo_uma_busca(self):
        nav = NavFalso({"k": busca(p1=pagina_de(M2, M1), p2=pagina_de(M3, inicio=1))})
        r = robo.verificar_keyword(nav, "k", [M1, M3], 20)
        self.assertTrue(r[M1]["encontrado"] and r[M3]["encontrado"])
        self.assertEqual(len(nav.lotes), 1)


class TestExecutar(Base):
    def setUp(self):
        super().setUp()
        store.salvar_watch("meli02", M1, "Escova de Canto")
        store.salvar_watch("meli02", M3, "escova de canto")
        store.salvar_watch("meli02", M1, "escova para cantos")
        self.sem_pausa = lambda: None

    def paginas(self):
        return {"escova de canto": busca(p1=pagina_de(M2, M1), p2=pagina_de(M3, inicio=1)),
                "escova para cantos": busca(p1=pagina_de(M2, M3))}

    def test_execucao_completa(self):
        nav = NavFalso(self.paginas())
        r = robo.executar("todas", nav=nav, pausa=self.sem_pausa)
        self.assertEqual((r["verificados"], r["termos"], r["encontrados"], r["fora"]), (3, 2, 2, 1))
        self.assertEqual(r["resumo"], "3 anúncios verificados em 2 termos · 2 encontrados · 1 fora das 20 primeiras páginas")
        self.assertEqual(r["erro"], "")
        res = {(x["mlb"], x["keyword"]): x for x in store.listar_resultados("meli02")}
        self.assertEqual(res[(M1, "escova de canto")]["posicao"], 2)
        self.assertIsNone(res[(M1, "escova para cantos")]["posicao"])
        self.assertEqual(len(res[(M1, "escova de canto")]["historico"]), 1)
        self.assertEqual(store.estado(), "idle")

    def test_conta_errada_aborta_sem_gravar(self):
        r = robo.executar("todas", nav=NavFalso(self.paginas(), viewer="MZCELL VENDAS"), pausa=self.sem_pausa)
        self.assertIn("MZCELL VENDAS", r["erro"])
        self.assertEqual(store.listar_resultados(), [])
        self.assertEqual(store.estado(), "idle")

    def test_captcha_no_meio_preserva_o_que_ja_foi_verificado(self):
        nav = NavFalso(self.paginas(), bloqueia_em=("escova para cantos", 1))
        r = robo.executar("todas", nav=nav, pausa=self.sem_pausa)
        self.assertIn("captcha", r["erro"])
        chaves = {x["keyword"] for x in store.listar_resultados()}
        self.assertEqual(chaves, {"escova de canto"})          # o termo bloqueado NÃO vira "não encontrado"

    def test_mudanca_de_5_posicoes_vai_para_o_resumo(self):
        robo.executar("todas", nav=NavFalso(self.paginas()), pausa=self.sem_pausa)
        subiu = self.paginas()
        subiu["escova de canto"][1] = pagina_de(M2, M3, "MLB1000000004", "MLB1000000005", "MLB1000000006",
                                                "MLB1000000007", M1)     # M1 foi de 2 para 7
        r = robo.executar("todas", nav=NavFalso(subiu), pausa=self.sem_pausa)
        self.assertEqual([(m["mlb"], m["de"], m["para"]) for m in r["mudancas"] if m["mlb"] == M1],
                         [(M1, 2, 7)])
        res = {(x["mlb"], x["keyword"]): x for x in store.listar_resultados()}
        self.assertEqual(res[(M1, "escova de canto")]["variacao"], "caiu")
        self.assertEqual(len(res[(M1, "escova de canto")]["historico"]), 2)

    def test_filtra_por_conta_e_par(self):
        store.salvar_watch("meli01", "MLB1000000009", "outro termo")
        self.assertEqual(set(robo.selecionar("meli01")), {"outro termo"})
        self.assertEqual(robo.selecionar("meli02", M1, "Escova  de canto"), {"escova de canto": [("meli02", M1)]})
        store.salvar_watch("meli02", M3, "escova de canto", ativo=False)
        self.assertEqual(robo.selecionar("meli02")["escova de canto"], [("meli02", M1)])

    def test_travada_nao_inicia_outra(self):
        self.assertTrue(store.iniciar_execucao())
        r = robo.executar("todas", nav=NavFalso(self.paginas()), pausa=self.sem_pausa)
        self.assertEqual(r["verificados"], 0)
        self.assertEqual(store.listar_resultados(), [])


class TestStoreEFila(Base):
    def test_termo_principal_unico_por_anuncio(self):
        a = store.salvar_watch("meli02", M1, "a")
        b = store.salvar_watch("meli02", M1, "b")
        self.assertTrue(a["principal"] and not b["principal"])
        store.salvar_watch("meli02", M1, "b", principal=True)
        self.assertEqual([w["keyword"] for w in store.listar_watch() if w["principal"]], ["b"])

    def test_novo_termo_herda_titulo_do_anuncio(self):
        store.salvar_watch("meli02", M1, "a", titulo="Escova de Canto")
        self.assertEqual(store.salvar_watch("meli02", M1, "b")["titulo"], "Escova de Canto")
        self.assertEqual(store.salvar_watch("meli02", M2, "b")["titulo"], "")

    def test_validacao(self):
        with self.assertRaises(store.ErroRadar):
            store.salvar_watch("meli02", "xx", "a")
        with self.assertRaises(store.ErroRadar):
            store.salvar_watch("meli02", M1, "")

    def test_resumo_do_produto_para_o_modal(self):
        self.assertEqual(store.resumo_produto("meli02", M1)["monitorado"], False)
        store.salvar_watch("meli02", M1, "a"); store.salvar_watch("meli02", M1, "b")
        store.registrar_resultados([{"conta": "meli02", "mlb": M1, "keyword": "a", "encontrado": True, "pagina": 2,
                                     "posicao": 61, "pagina_pos": 11, "ad_type": "organico", "max_pagina": 2}])
        r = store.resumo_produto("meli02", M1)
        self.assertEqual((r["keyword_principal"], r["principal"]["pagina"], r["principal"]["posicao"]), ("a", 2, 61))
        self.assertEqual(len(r["termos"]), 2)

    def test_historico_limitado(self):
        for i in range(store.HIST_MAX + 10):
            store.registrar_resultados([{"conta": "c", "mlb": M1, "keyword": "a", "encontrado": True, "pagina": 1,
                                         "posicao": i + 1, "pagina_pos": 1, "ad_type": "organico", "max_pagina": 1}])
        self.assertEqual(len(store.listar_resultados()[0]["historico"]), store.HIST_MAX)

    def test_fila_pedido_running_idle(self):
        self.assertEqual(store.estado(), "idle")
        store.pedir_verificacao("todas", autor="ana")
        self.assertEqual(store.estado(), "queued")
        with self.assertRaises(store.ErroRadar):
            store.pedir_verificacao("todas")
        pedido = store.status()["pedido"]["pedido_em"]
        store.iniciar_execucao(pedido)
        self.assertEqual(store.estado(), "running")
        store.finalizar_execucao("ok")
        self.assertEqual(store.estado(), "idle")

    def test_planilha_nao_sobrescreve_registro_local_mais_novo(self):
        w = store.salvar_watch("meli02", M1, "a", titulo="novo")
        antigo = dict(w, titulo="velho", atualizado_em="2020-01-01T00:00:00.000Z")
        store.aplicar_watch([{"chave": store.chave("meli02", M1, "a"), "dados": json.dumps(antigo), "removido": False}])
        self.assertEqual(store.listar_watch()[0]["titulo"], "novo")
        mais_novo = dict(w, titulo="da planilha", atualizado_em="2999-01-01T00:00:00.000Z")
        store.aplicar_watch([{"chave": store.chave("meli02", M1, "a"), "dados": json.dumps(mais_novo), "removido": False}])
        self.assertEqual(store.listar_watch()[0]["titulo"], "da planilha")
        store.aplicar_watch([{"chave": store.chave("meli02", M1, "a"), "dados": "{}", "removido": True}])
        self.assertEqual(store.listar_watch(), [])

    def test_publica_nas_tres_colecoes(self):
        store.salvar_watch("meli02", M1, "a")
        store.registrar_resultados([{"conta": "meli02", "mlb": M1, "keyword": "a", "encontrado": False, "max_pagina": 20}])
        store.pedir_verificacao()
        self.assertEqual({c.args[0] for c in self.pub.call_args_list}, {"radar_watch", "radar", "radar_status"})


class TestAgendador(Base):
    def setUp(self):
        super().setUp()
        with open(robo.CONFIG_PATH, "w") as f:
            json.dump({"robo_ativo": True, "horarios": ["08:30", "13:30", "19:30"]}, f)
        store.salvar_watch("meli02", M1, "k")
        self.nav = NavFalso({"k": busca(p1=pagina_de(M1))})
        self.kw = {"nav": self.nav, "pausa": lambda: None}

    def test_antes_do_primeiro_horario_nao_roda(self):
        self.assertIsNone(robo.tique(datetime(2026, 10, 24, 7, 0), **self.kw))

    def test_roda_uma_vez_por_horario(self):
        self.assertIsNotNone(robo.tique(datetime(2026, 10, 24, 8, 31), **self.kw))
        self.assertIsNone(robo.tique(datetime(2026, 10, 24, 8, 32), **self.kw))
        self.assertIsNotNone(robo.tique(datetime(2026, 10, 24, 13, 31), **self.kw))

    def test_robo_desligado_por_8h_roda_um_catchup_so(self):
        self.assertIsNotNone(robo.tique(datetime(2026, 10, 24, 20, 0), **self.kw))
        self.assertIsNone(robo.tique(datetime(2026, 10, 24, 20, 1), **self.kw))

    def test_pedido_da_fila_e_atendido(self):
        robo.tique(datetime(2026, 10, 24, 20, 0), **self.kw)
        store.pedir_verificacao("meli02", M1, "k")
        r = robo.tique(datetime(2026, 10, 24, 20, 5), **self.kw)
        self.assertEqual(r["verificados"], 1)
        self.assertEqual(store.estado(), "idle")

    def test_robo_so_liga_com_a_flag(self):
        os.remove(robo.CONFIG_PATH)
        self.assertIsNone(robo.iniciar_robo())


class TestRouter(Base):
    def setUp(self):
        super().setUp()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from marketplaces.radar.router import router
        app = FastAPI(); app.include_router(router)
        self.c = TestClient(app)

    def test_fluxo_basico(self):
        r = self.c.post("/radar/watch", json={"conta": "meli02", "mlb": M1, "keyword": "Escova de canto", "autor": "ana"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.post("/radar/watch", json={"conta": "meli02", "mlb": "x", "keyword": "a"}).status_code, 400)
        p = self.c.get(f"/radar/produto/meli02/{M1}").json()
        self.assertTrue(p["monitorado"] and p["principal"] is None)
        self.assertEqual(self.c.get("/radar/estado?conta=meli02").json()["estado"], "idle")
        self.assertEqual(self.c.post("/radar/executar", json={"escopo": "meli02"}).status_code, 200)
        self.assertEqual(self.c.post("/radar/executar", json={}).status_code, 409)
        self.assertEqual(self.c.get("/radar/estado").json()["estado"], "queued")
        self.assertEqual(self.c.delete("/radar/watch", params={"conta": "meli02", "mlb": M1, "keyword": "escova de canto"}).status_code, 200)
        self.assertEqual(self.c.delete("/radar/watch", params={"conta": "meli02", "mlb": M1, "keyword": "escova de canto"}).status_code, 404)

    def test_pagina_html(self):
        r = self.c.get("/radar")
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
