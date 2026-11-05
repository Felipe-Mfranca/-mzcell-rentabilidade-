"""Tela de Usuários: arquivo temporário e planilha simulada; nunca toca o usuarios.json nem a planilha reais."""
import json
import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import auth_service as a
import main


class Base(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.b = os.path.join(d.name, "u.json")
        for alvo, nome, v in ((a, "USUARIOS_BASE", self.b), (a, "USUARIOS_LOCAL", os.path.join(d.name, "l", "u.json")),
                              (main.shared_store, "sincronizar", lambda *x: {}), (main, "log_atividade", lambda *x: None)):
            p = mock.patch.object(alvo, nome, v); p.start(); self.addCleanup(p.stop)
        p = mock.patch("shared_store.enviar_async"); self.env = p.start(); self.addCleanup(p.stop)
        with open(self.b, "w", encoding="utf-8") as f:
            json.dump([self.reg("zicri", ["todos"]), self.reg("michel", ["todos"]), self.reg("felipe", ["todos"], "master")], f)

    @staticmethod
    def reg(login, modulos, role="adminplus"):
        return {"login": login, "nome": login.title(), "role": role, "senha": a.h("segredo1"), "email": login + "@x.com", "modulos": modulos,
                "primeiro_acesso": False, "senha_atualizada_em": "2026-01-01T00:00:00.000Z"}


class Regras(Base):
    def test_lista_nao_traz_senha(self):
        self.assertNotIn("senha", json.dumps(a.listar_usuarios()))

    def test_editar_restringe_publica_com_carimbo_novo_e_preserva_a_senha(self):
        u, mudou = a.editar_usuario("felipe", {"modulos": ["expedicao"], "role": "operador", "area": "Expedição", "senha": "ignorada"})
        self.assertTrue(mudou)
        self.assertEqual((u["modulos"], u["role"], u["area"], u["senha"]), (["expedicao"], "operador", "Expedição", a.h("segredo1")))
        self.assertGreater(self.env.call_args.kwargs["ts"], "2026-01-01")
        col, chave, dados = self.env.call_args.args[:3]
        self.assertEqual((col, chave, dados["modulos"], dados["area"]), ("usuarios", "felipe", ["expedicao"], "Expedição"))

    def test_edicao_chega_em_outro_pc(self):
        u, _ = a.editar_usuario("felipe", {"modulos": ["expedicao"], "ativo": False})
        dados = self.env.call_args.args[2]
        with open(self.b, "w", encoding="utf-8") as f:
            json.dump([self.reg("felipe", ["todos"], "master")], f)
        a.aplicar_usuarios([{"dados": json.dumps(dados), "atualizado_em": u["senha_atualizada_em"]}])
        novo = a.load_usuarios()[0]
        self.assertEqual((novo["modulos"], novo["ativo"]), (["expedicao"], False))

    def test_bloqueado_nao_entra(self):
        self.assertIsNotNone(a.verificar_login("felipe", "segredo1"))
        a.editar_usuario("felipe", {"ativo": False})
        self.assertIsNone(a.verificar_login("felipe", "segredo1"))

    def test_modulos_invalidos_e_vazio_sao_recusados_sem_gravar(self):
        for m in ([], ["inventado"], None):
            with self.assertRaises(ValueError):
                a.editar_usuario("felipe", {"modulos": m})
        self.assertEqual(a.load_usuarios()[2]["modulos"], ["todos"])
        self.env.assert_not_called()

    def test_admin_nao_perde_acesso(self):
        for dados in ({"ativo": False}, {"modulos": ["sac"]}):
            with self.assertRaises(ValueError):
                a.editar_usuario("michel", dados)
        self.assertEqual(a.load_usuarios()[1]["modulos"], ["todos"])

    def test_criar(self):
        u = a.criar_usuario({"login": " Nova ", "nome": "Nova", "senha": "provisoria", "modulos": ["sac"], "cargo": "Analista"})
        self.assertEqual((u["login"], u["primeiro_acesso"], u["modulos"], u["cargo"]), ("nova", True, ["sac"], "Analista"))
        self.assertIsNotNone(a.verificar_login("nova", "provisoria"))
        for ruim in ({"login": "nova", "nome": "X", "senha": "provisoria", "modulos": ["sac"]},
                     {"login": "a b", "nome": "X", "senha": "provisoria", "modulos": ["sac"]},
                     {"login": "ok", "nome": "X", "senha": "123", "modulos": ["sac"]},
                     {"login": "ok", "nome": "", "senha": "provisoria", "modulos": ["sac"]},
                     {"login": "ok", "nome": "X", "senha": "provisoria"}):
            with self.assertRaises(ValueError):
                a.criar_usuario(ruim)
        self.assertEqual(len(a.load_usuarios()), 4)


class Rotas(Base):
    def entrar(self, login):
        c = TestClient(main.app, client=("192.168.15.50", 5000))
        self.assertEqual(c.post("/auth/login", json={"login": login, "senha": "segredo1"}).status_code, 200)
        return c

    def test_sem_sessao_401_mesmo_no_servidor(self):
        self.assertEqual(TestClient(main.app).get("/usuarios").status_code, 401)
        self.assertEqual(TestClient(main.app).put("/usuarios/felipe", json={"ativo": False}).status_code, 401)

    def test_quem_nao_administra_recebe_403(self):
        c = self.entrar("felipe")
        self.assertEqual(c.get("/usuarios").status_code, 403)
        self.assertEqual(c.post("/usuarios", json={"login": "x", "nome": "X", "senha": "provisoria", "modulos": ["sac"]}).status_code, 403)
        self.assertEqual(a.load_usuarios()[2]["modulos"], ["todos"])

    def test_admin_edita_e_a_sessao_da_pessoa_cai(self):
        alvo, adm = self.entrar("felipe"), self.entrar("zicri")
        self.assertEqual(alvo.get("/usuarios").status_code, 403)
        r = adm.put("/usuarios/felipe", json={"modulos": ["expedicao"]})
        self.assertEqual((r.status_code, r.json()["acesso_mudou"]), (200, True))
        self.assertEqual(alvo.get("/usuarios").status_code, 401)
        self.assertNotIn("senha", adm.get("/usuarios").text)
        self.assertEqual(adm.put("/usuarios/ninguem", json={"nome": "x"}).status_code, 404)
        self.assertEqual(adm.put("/usuarios/felipe", json={"modulos": []}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
