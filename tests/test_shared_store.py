"""Banco compartilhado (Google Sheets): fila offline, leitura incremental e aplicação local, sem rede."""
import json
import os
import tempfile
import unittest
from unittest import mock

import shared_store as ss


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        for nome, arq in (("CONFIG_PATH", "cfg.json"), ("FILA_PATH", "fila.json"), ("ESTADO_PATH", "estado.json")):
            p = mock.patch.object(ss, nome, os.path.join(self.d.name, arq))
            p.start()
            self.addCleanup(p.stop)
        json.dump({"url": "https://exemplo.invalid/exec", "token": "segredo-teste-123", "intervalo_seg": 60},
                  open(ss.CONFIG_PATH, "w"))


class Config(Base):
    def test_sem_config_nao_faz_nada(self):
        os.remove(ss.CONFIG_PATH)
        self.assertFalse(ss.configurado())
        self.assertFalse(ss.enviar("comentarios", "a|b|1", {"texto": "x"}))
        self.assertEqual(ss.sincronizar({}), {"configurado": False})

    def test_placeholder_nao_conta_como_configurado(self):
        json.dump({"url": "COLE_AQUI", "token": "COLE_AQUI"}, open(ss.CONFIG_PATH, "w"))
        self.assertFalse(ss.configurado())


class Envio(Base):
    def test_envio_ok_esvazia_a_fila_e_manda_o_token(self):
        with mock.patch.object(ss, "_post", return_value={"ok": True, "gravados": 1}) as p:
            self.assertTrue(ss.enviar("comentarios", "meli01|MLB1|9", {"texto": "olá"}, "kauan"))
        corpo = p.call_args.args[0]
        self.assertEqual((corpo["acao"], corpo["colecao"]), ("gravar", "comentarios"))
        r = corpo["registros"][0]
        self.assertEqual((r["chave"], r["autor"], r["removido"]), ("meli01|MLB1|9", "kauan", False))
        self.assertEqual(json.loads(r["dados"]), {"texto": "olá"})
        self.assertEqual(json.load(open(ss.FILA_PATH)), [])

    def test_sem_internet_fica_na_fila_e_sobe_depois(self):
        with mock.patch.object(ss, "_post", side_effect=ss.ErroCompartilhado("sem conexão")):
            self.assertFalse(ss.enviar("posicoes", "meli01|MLB1", {"pagina": 1, "posicao": 3}))
            self.assertFalse(ss.enviar("clips", "meli01|MLB2", {"clip": True}))
        self.assertEqual(len(json.load(open(ss.FILA_PATH))), 2)
        with mock.patch.object(ss, "_post", return_value={"ok": True}) as p:
            self.assertEqual(ss.esvaziar_fila(), 2)
        self.assertEqual(p.call_count, 2)                     # um envio por coleção
        self.assertEqual(json.load(open(ss.FILA_PATH)), [])

    def test_falha_parcial_mantem_so_o_que_nao_subiu(self):
        with mock.patch.object(ss, "_post", side_effect=ss.ErroCompartilhado("x")):
            ss.enviar("posicoes", "a|1", {}); ss.enviar("clips", "a|2", {"clip": False})

        def so_posicoes(corpo, timeout=25):
            if corpo["colecao"] == "clips":
                raise ss.ErroCompartilhado("falhou")
            return {"ok": True}
        with mock.patch.object(ss, "_post", side_effect=so_posicoes):
            self.assertEqual(ss.esvaziar_fila(), 1)
        self.assertEqual([x["colecao"] for x in json.load(open(ss.FILA_PATH))], ["clips"])

    def test_colecao_desconhecida_e_ignorada(self):
        self.assertFalse(ss.enviar("usuarios", "x", {}))


class Leitura(Base):
    def test_puxar_usa_marcador_e_so_avanca_depois_de_ler(self):
        reg = [{"chave": "a|b|1", "dados": "{}", "atualizado_em": "2026-10-20T10:00:00.000Z", "autor": "", "removido": False},
               {"chave": "a|b|2", "dados": "{}", "atualizado_em": "2026-10-20T11:00:00.000Z", "autor": "", "removido": False}]
        with mock.patch.object(ss, "_post", return_value={"ok": True, "registros": reg}) as p:
            self.assertEqual(len(ss.puxar("comentarios")), 2)
        self.assertEqual(p.call_args.args[0]["desde"], "")
        with mock.patch.object(ss, "_post", return_value={"ok": True, "registros": []}) as p:
            ss.puxar("comentarios")
        self.assertEqual(p.call_args.args[0]["desde"], "2026-10-20T11:00:00.000Z")

    def test_erro_na_leitura_nao_move_o_marcador(self):
        with mock.patch.object(ss, "_post", side_effect=ss.ErroCompartilhado("x")):
            with self.assertRaises(ss.ErroCompartilhado):
                ss.puxar("clips")
        self.assertFalse(os.path.exists(ss.ESTADO_PATH))

    def test_sincronizar_aplica_e_resume(self):
        recebidos = []
        reg = [{"chave": "c|m", "dados": "{}", "atualizado_em": "2026-10-20T10:00:00.000Z", "autor": "", "removido": False}]
        with mock.patch.object(ss, "_post", return_value={"ok": True, "registros": reg}):
            r = ss.sincronizar({"clips": recebidos.extend})
        self.assertEqual(r["recebidos"], {"clips": 1})
        self.assertEqual(len(recebidos), 1)

    def test_post_nao_vaza_o_token_na_excecao(self):
        import httpx
        with mock.patch("httpx.post", side_effect=httpx.ConnectError("falhou ?token=segredo-teste-123")):
            with self.assertRaises(ss.ErroCompartilhado) as c:
                ss._post({"acao": "ping"})
        self.assertNotIn("segredo-teste-123", str(c.exception))


class Aplicacao(unittest.TestCase):
    """As funções de main.py que gravam no disco local o que veio da planilha."""

    def setUp(self):
        import main
        self.main = main
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        for obj, nome, arq in ((main, "_anotacoes_path", None), (main, "POSICOES_PATH", "pos.json")):
            pass
        self.pos = os.path.join(self.d.name, "pos.json")
        self.ano = os.path.join(self.d.name, "ano.json")
        for p in (mock.patch.object(main, "POSICOES_PATH", self.pos), mock.patch.object(main, "_anotacoes_path", lambda: self.ano)):
            p.start()
            self.addCleanup(p.stop)

    def test_comentario_novo_remoto_entra_e_remocao_sai(self):
        r = {"chave": "meli01|MLB1|20261020", "dados": json.dumps({"texto": "do Kauan", "criado_em": "20/10/2026 10:00", "autor": "kauan"}),
             "atualizado_em": "2026-10-20T13:00:00.000Z", "autor": "kauan", "removido": False}
        self.main._aplicar_comentarios([r])
        self.main._aplicar_comentarios([r])                       # idempotente: não duplica
        lista = json.load(open(self.ano))["meli01"]["MLB1"]
        self.assertEqual([(a["id"], a["texto"], a["autor"]) for a in lista], [("20261020", "do Kauan", "kauan")])
        self.main._aplicar_comentarios([{**r, "removido": True, "dados": "{}"}])
        self.assertEqual(json.load(open(self.ano))["meli01"]["MLB1"], [])

    def test_posicao_mais_antiga_nao_sobrescreve_a_mais_nova(self):
        novo = {"pagina": 1, "posicao": 2, "keyword": "x", "atualizado_em": "2026-10-20T12:00:00.000Z"}
        json.dump({"meli01": {"MLB1": novo}}, open(self.pos, "w"))
        antigo = {"chave": "meli01|MLB1", "dados": json.dumps({"pagina": 3, "posicao": 9, "atualizado_em": "2026-10-20T08:00:00.000Z"}),
                  "atualizado_em": "2026-10-20T08:00:00.000Z"}
        self.main._aplicar_posicoes([antigo])
        self.assertEqual(json.load(open(self.pos))["meli01"]["MLB1"]["posicao"], 2)
        recente = {**antigo, "dados": json.dumps({"pagina": 1, "posicao": 1, "atualizado_em": "2026-10-20T15:00:00.000Z"}),
                   "atualizado_em": "2026-10-20T15:00:00.000Z"}
        self.main._aplicar_posicoes([recente])
        self.assertEqual(json.load(open(self.pos))["meli01"]["MLB1"]["posicao"], 1)


if __name__ == "__main__":
    unittest.main()
