"""CMV amarrado às composições do Bling (kits e clones) — sem rede, base em arquivo temporário."""
import os
import tempfile
import unittest
from unittest import mock

import cmv_base
import cmv_composicao as cc

KITS = {
    "KIT-FACA-9": {b: [("KIT-FACA6", 1), ("AFIADOR", 1), ("GARRA", 1), ("TABUA", 1)] for b in ("bling01", "bling02", "bling03")},
    "KIT-FACA6": {"bling01": [("FACA", 6)]},
    "CONTROLPC": {"bling01": [("CONTROLPS4-PRETO", 1)], "bling02": [("CONTROLPS4-PRETO", 1)]},
    "DIVERGE": {"bling01": [("TABUA", 1)], "bling02": [("TABUA", 2)]},
}


class TestComposicao(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.p = [mock.patch.object(cmv_base, "PATH", os.path.join(self.tmp, "cmv_base.json")),
                  mock.patch.object(cc, "_catalogo", lambda: {"kits": KITS, "gerado_em": "2026-11-05T08:00:00"}),
                  mock.patch.object(cmv_base, "publicar", lambda *a, **k: None),
                  mock.patch.object(cmv_base, "propagar_ml", lambda: {})]
        [x.start() for x in self.p]
        cmv_base.salvar({k: {"sku": k, "cmv": v} for k, v in
                         {"FACA": 5.0, "AFIADOR": 8.0, "GARRA": 4.0, "TABUA": 10.0, "CONTROLPS4-PRETO": 50.0,
                          "KIT-FACA-9": 99.0, "CONTROLPC": 45.0}.items()})

    def tearDown(self):
        [x.stop() for x in self.p]

    def test_kit_abre_os_componentes_e_soma(self):
        d = cc.composicao("kit-faca-9")
        self.assertEqual(d["tipo"], "kit")
        self.assertEqual([c["sku"] for c in d["componentes"]], ["KIT-FACA6", "AFIADOR", "GARRA", "TABUA"])
        self.assertEqual(d["componentes"][0]["cmv"], 30.0)          # kit interno: 6 facas × 5
        self.assertTrue(d["componentes"][0]["e_kit"])
        self.assertEqual(d["cmv"], 52.0)
        self.assertEqual(d["cmv_base_kit"], 99.0)                    # valor antigo da planilha, mostrado para conferir

    def test_clone_abre_o_componente(self):
        d = cc.composicao("CONTROLPC")
        self.assertEqual((d["tipo"], d["componentes"][0]["sku"], d["cmv"]), ("clone", "CONTROLPS4-PRETO", 50.0))

    def test_salvar_componente_recalcula_kits_que_usam(self):
        r = cc.salvar_cmv("faca", 6.0, "zicri")
        self.assertEqual({k["sku"]: k["para"] for k in r["kits"]}, {"KIT-FACA6": 36.0, "KIT-FACA-9": 58.0})
        itens = cmv_base.carregar()["itens"]
        self.assertEqual((itens["FACA"]["cmv"], itens["KIT-FACA-9"]["cmv"]), (6.0, 58.0))

    def test_salvar_clone_pelo_componente(self):
        r = cc.salvar_cmv("CONTROLPS4-PRETO", 48.0, "zicri")
        self.assertEqual(r["kits"], [{"sku": "CONTROLPC", "de": 45.0, "para": 48.0}])

    def test_nao_grava_direto_no_kit(self):
        with self.assertRaises(ValueError):
            cc.salvar_cmv("KIT-FACA-9", 10.0, "zicri")

    def test_composicao_divergente_nao_recalcula(self):
        r = cc.salvar_cmv("TABUA", 12.0, "zicri")
        self.assertIn({"sku": "DIVERGE", "motivo": "composição diferente entre Blings"}, r["kits_pendentes"])
        self.assertTrue(cc.composicao("DIVERGE")["diverge"])
        self.assertNotIn("DIVERGE", cmv_base.carregar()["itens"])

    def test_componente_sem_cmv_deixa_kit_pendente(self):
        cmv_base.salvar({"TABUA": {"sku": "TABUA", "cmv": 10.0}})
        r = cc.salvar_cmv("GARRA", 4.0, "zicri")
        self.assertEqual(r["kits"], [])
        self.assertEqual({k["sku"] for k in r["kits_pendentes"]}, {"KIT-FACA-9"})

    def test_preencher_kits_so_os_sem_cmv(self):
        sim = cc.preencher_kits()
        self.assertEqual({k["sku"]: k["cmv"] for k in sim["kits"]}, {"KIT-FACA6": 30.0})   # KIT-FACA-9 e CONTROLPC já têm valor
        self.assertNotIn("KIT-FACA6", cmv_base.carregar()["itens"])                     # simulação não grava
        cc.preencher_kits(True, "zicri")
        self.assertEqual(cmv_base.carregar()["itens"]["KIT-FACA6"]["cmv"], 30.0)

    def test_importacao_recalcula_kits_dos_componentes(self):
        itens = cmv_base.carregar()["itens"]
        itens["CONTROLPS4-PRETO"]["cmv"] = 40.0                 # como se a planilha tivesse trazido o componente
        cmv_base.salvar(itens)
        r = cc.recalcular_kits(["controlps4-preto", "KIT-FACA-9"])   # kit na planilha é ignorado: vale a soma
        self.assertEqual(r["recalculados"], 1)
        self.assertEqual(cmv_base.carregar()["itens"]["CONTROLPC"]["cmv"], 40.0)

    def planilha(self, linhas):
        import io, openpyxl
        wb = openpyxl.Workbook()
        wb.active.append(["SKU", "CMV"])
        for x in linhas:
            wb.active.append(x)
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()

    def test_lote_simulado_nao_grava(self):
        antes = cmv_base.carregar()["itens"]
        r = cc.gravar_lote({"faca": 6.0}, "zicri", confirmar=False)
        self.assertFalse(r["confirmado"])
        self.assertEqual(r["alterados"], [{"sku": "FACA", "de": 5.0, "para": 6.0}])
        self.assertEqual(cmv_base.carregar()["itens"], antes)

    def test_lote_grava_recalcula_e_recusa_kit(self):
        pub = []
        with mock.patch.object(cmv_base, "publicar", lambda m, a="": pub.extend(m)):
            r = cc.gravar_lote({"faca": 6.0, "AFIADOR": 8.0, "KIT-FACA-9": 1.0, "TABUA": "abc"}, "zicri", "carga de planilha")
        self.assertEqual([x["sku"] for x in r["recusados"]], ["KIT-FACA-9", "TABUA"])
        self.assertEqual(r["iguais"], 1)                                     # AFIADOR já valia 8: não regrava
        itens = cmv_base.carregar()["itens"]
        self.assertEqual(itens["FACA"]["aba"], "carga de planilha")
        self.assertEqual(itens["KIT-FACA-9"]["cmv"], 6 * 6 + 8 + 4 + 10)     # kit vale a soma, não o valor enviado
        self.assertIn("FACA", pub)
        self.assertNotIn("AFIADOR", pub)

    def test_um_sku_e_planilha_gravam_igual(self):
        cc.salvar_cmv("faca", 7.0, "zicri")
        pelo_modal = cmv_base.carregar()["itens"]["KIT-FACA-9"]["cmv"]
        cc.salvar_cmv("faca", 5.0, "zicri")
        cc.carga_planilha(self.planilha([["FACA", 7.0]]), "a.xlsx", "zicri", True)
        self.assertEqual(cmv_base.carregar()["itens"]["KIT-FACA-9"]["cmv"], pelo_modal)

    def test_carga_simula_por_padrao_e_mostra_linhas_ruins(self):
        r = cc.carga_planilha(self.planilha([["FACA", 9.0], ["GARRA", None], ["CONTROLPC", 3.0]]), "a.xlsx", "zicri")
        self.assertEqual(cmv_base.carregar()["itens"]["FACA"]["cmv"], 5.0)
        self.assertEqual(len(r["alterados"]), 1)
        self.assertEqual(len(r["ignorados"]), 1)
        self.assertEqual(r["recusados"][0]["sku"], "CONTROLPC")

    def test_carga_recusa_gravar_com_catalogo_incompleto(self):
        with mock.patch.object(cc, "_catalogo", lambda: {"kits": KITS, "gerado_em": "x", "erros": {"bling03": "timeout"}}):
            self.assertIn("bling03", cc.carga_planilha(self.planilha([["FACA", 9.0]]), "a", "z")["catalogo_erros"])
            with self.assertRaises(ValueError):
                cc.carga_planilha(self.planilha([["FACA", 9.0]]), "a", "z", True)
        self.assertEqual(cmv_base.carregar()["itens"]["FACA"]["cmv"], 5.0)

    def test_lista_situacao_de_cada_sku(self):
        cmv_base.salvar({k: {"sku": k, "cmv": v} for k, v in {"FACA": 5.0, "KIT-FACA-9": 99.0}.items()})
        with mock.patch("marketplaces.balanco.catalogo.ler", lambda: {"blings": {"b": {"skus": {"NOVO": {"nome": "Novo"}}}}}), \
                mock.patch("produtos_store.ler", lambda: {"MLB1": {"sku": "faca", "conta": "meli01"}, "MLB2": {"sku": "NOVO", "conta": "meli02"}}), \
                mock.patch.object(cmv_base, "ASIN_PATH", os.path.join(self.tmp, "nao_existe.json")):
            d = {x["sku"]: x for x in cc.lista()["linhas"]}
        self.assertEqual((d["FACA"]["status"], d["FACA"]["contas"]), ("ok", {"meli01": 1}))
        self.assertEqual(d["NOVO"]["status"], "sem_cmv")
        self.assertEqual((d["KIT-FACA6"]["status"], d["KIT-FACA6"]["soma"]), ("a_preencher", 30.0))
        self.assertEqual((d["KIT-FACA-9"]["status"], d["KIT-FACA-9"]["soma"], d["KIT-FACA-9"]["cmv"]), ("ok", None, 99.0))

    def test_outro_pc_nao_altera_cmv_em_nome_de_outra_pessoa(self):
        from fastapi.testclient import TestClient
        import main
        from marketplaces import acesso_rede
        c = TestClient(main.app, client=("192.168.15.50", 5000))
        c.cookies.set(acesso_rede.COOKIE, acesso_rede.criar_sessao("kauan"))
        corpo = {"sku": "FACA", "cmv": 1.0, "autor": "zicri"}
        self.assertEqual(c.post("/cmv/sku", json=corpo).status_code, 403)
        self.assertEqual(c.get("/cmv/lista?autor=zicri").status_code, 403)
        self.assertEqual(c.post("/cmv/aplicar/meli01?confirmar=true&autor=zicri").status_code, 403)
        c.cookies.set(acesso_rede.COOKIE, acesso_rede.criar_sessao("zicri"))
        with mock.patch.object(main, "_comentar_cmv", lambda *a, **k: 0), mock.patch.object(main, "log_atividade", lambda *a, **k: None):
            self.assertEqual(c.post("/cmv/sku", json=dict(corpo, autor="kauan")).status_code, 200)
        self.assertEqual(cmv_base.carregar()["itens"]["FACA"]["autor"], "zicri")   # vale a sessão, não o nome enviado

    def test_planilha_de_carga_so_tem_componentes(self):
        import io, openpyxl
        with mock.patch("marketplaces.balanco.catalogo.ler", lambda: {"blings": {"bling01": {"skus": {"FACA": {"nome": "Faca"}}}}}):
            wb = openpyxl.load_workbook(io.BytesIO(cc.planilha_carga()))
        skus = [r[0] for r in wb["CMV"].iter_rows(min_row=2, values_only=True)]
        self.assertIn("FACA", skus)
        self.assertNotIn("KIT-FACA-9", skus)
        self.assertEqual(set(cmv_base.ler_planilha(open_bytes(wb))["itens"]), set(skus) - {s for s in skus if cmv_base.carregar()["itens"].get(s) is None})


def open_bytes(wb):
    import io
    b = io.BytesIO(); wb.save(b); return b.getvalue()


if __name__ == "__main__":
    unittest.main()
