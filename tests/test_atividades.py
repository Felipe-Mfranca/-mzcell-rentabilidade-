"""Registro de atividades: gravação, área e nome da ação, resumo por pessoa, acesso e checkout do Bling. Sem rede."""
import os
import tempfile
import unittest
from unittest import mock

import atividades as at


class TestAtividades(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        p = mock.patch.object(at, "DB_PATH", os.path.join(self.d.name, "a.db"))
        p.start(); self.addCleanup(p.stop)

    def test_requisicao_vira_evento_com_area_e_nome(self):
        at.registrar_requisicao("ana", "192.168.15.20", "POST", "/cmv/sku", "/cmv/sku", {}, 200)
        at.registrar_requisicao("bia", "192.168.15.21", "POST", "/posicoes/meli01/MLB1", "/posicoes/{conta}/{mlb:path}", {"conta": "meli01", "mlb": "MLB1"}, 200)
        at.registrar_requisicao("", "192.168.15.22", "POST", "/montagem/envios/F1/iniciar", "/montagem/envios/{frete}/iniciar", {"frete": "F1"}, 200)
        at.registrar_requisicao("ana", "x", "GET", "/cmv/lista", "/cmv/lista", {}, 200)          # leitura não entra
        at.registrar_requisicao("ana", "x", "POST", "/auth/login", "/auth/login", {}, 200)       # login tem registro próprio
        ev = {e["usuario"]: e for e in at.eventos("2000-01-01", "2999-12-31")}
        self.assertEqual(len(ev), 3)
        self.assertEqual((ev["ana"]["area"], ev["ana"]["acao"]), ("Anúncios", "Ajustou CMV"))
        self.assertEqual((ev["bia"]["acao"], ev["bia"]["objeto"], ev["bia"]["detalhe"]), ("Salvou posição", "MLB1", "conta meli01"))
        self.assertEqual((ev["sem login"]["area"], ev["sem login"]["acao"]), ("Expedição", "Iniciou montagem do Full"))
        self.assertEqual(at.area_da_rota("/qualquer"), "Outros")

    def test_resumo_por_pessoa_e_dia_com_hora_extra(self):
        for ts in ("2026-11-09T07:50:00-03:00", "2026-11-09T10:00:00-03:00", "2026-11-09T18:30:00-03:00", "2026-11-08T09:00:00-03:00"):
            at.registrar("ana", "Anúncios", "Salvou posição", ts=ts)
        at.registrar("ana", "Anúncios", "Ajustou CMV", status=400, ts="2026-11-09T11:00:00-03:00")   # erro não conta
        r = {d["dia"]: d for d in at.resumo("2026-11-01", "2026-11-30")}
        self.assertEqual((r["2026-11-09"]["total"], r["2026-11-09"]["fora_horario"]), (3, 2))
        self.assertEqual(r["2026-11-09"]["primeira"][11:16], "07:50")
        self.assertEqual(r["2026-11-08"]["fora_horario"], 1)                                      # domingo
        self.assertEqual(r["2026-11-09"]["acoes"], {"Salvou posição": 3})

    def test_so_zicri_e_michel_veem(self):
        self.assertTrue(at.pode_ver("Zicri"))
        self.assertTrue(at.pode_ver("michel"))
        self.assertFalse(at.pode_ver("eduardo"))
        self.assertFalse(at.pode_ver(""))

    def test_checkout_do_bling_primeira_leitura_so_guarda_a_foto(self):
        vistos = {}
        p = lambda i, s: {"id": i, "numero": i, "situacao": {"id": s}}
        self.assertEqual(at.conferir_checkout("bling01", {"A": p(1, 24), "B": p(2, 6)}, vistos, "2026-11-09T09:00:00-03:00"), [])
        novos = at.conferir_checkout("bling01", {"A": p(1, 24), "B": p(2, 24), "C": p(3, 6)}, vistos, "2026-11-09T09:02:00-03:00")
        self.assertEqual([n["objeto"] for n in novos], ["B"])
        self.assertIn("2 min", novos[0]["detalhe"])
        self.assertEqual(at.conferir_checkout("bling01", {"B": p(2, 24)}, vistos, "2026-11-09T09:04:00-03:00"), [])   # não repete

    def test_middleware_registra_gravacao_com_usuario_da_sessao(self):
        import time
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from marketplaces import acesso_rede
        app = FastAPI()

        @app.post("/tags/{conta}/{mlb}")
        def marcar(conta: str, mlb: str):
            return {"ok": True}

        @app.get("/tags/{conta}")
        def ler(conta: str):
            return {}
        at.instalar(app)
        cli = TestClient(app)
        cli.cookies.set(acesso_rede.COOKIE, acesso_rede.criar_sessao("ana"))
        cli.post("/tags/meli02/MLB9")
        cli.get("/tags/meli02")
        for _ in range(50):
            ev = at.eventos("2000-01-01", "2999-12-31")
            if ev:
                break
            time.sleep(0.05)
        self.assertEqual([(e["usuario"], e["acao"], e["objeto"], e["detalhe"]) for e in ev], [("ana", "Marcou tag", "MLB9", "conta meli02")])
        acesso_rede.encerrar_sessoes("ana")


if __name__ == "__main__":
    unittest.main()
