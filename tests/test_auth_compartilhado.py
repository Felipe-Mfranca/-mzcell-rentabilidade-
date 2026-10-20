import json, os, tempfile, unittest
from unittest import mock
import auth_service as a

class TrocaDeSenha(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.b = os.path.join(d.name, "u.json"); self.l = os.path.join(d.name, "l", "u.json")
        for n, v in (("USUARIOS_BASE", self.b), ("USUARIOS_LOCAL", self.l)):
            p = mock.patch.object(a, n, v); p.start(); self.addCleanup(p.stop)
        json.dump([{"login": "ana", "nome": "Ana", "role": "viewer", "senha": a.h("velha"), "primeiro_acesso": False}], open(self.b, "w"))
    def test_troca_publica_e_outro_pc_aplica(self):
        with mock.patch("shared_store.enviar_async") as env:
            a.atualizar_email_senha("ana", "a@x.com", "nova123")
        col, chave, dados, autor = env.call_args.args[:4]
        self.assertEqual((col, chave), ("usuarios", "ana"))
        reg = {"dados": json.dumps(dados), "atualizado_em": dados["senha_atualizada_em"]}
        # "outro PC": usuario com a senha velha e sem carimbo
        json.dump([{"login": "ana", "nome": "Ana", "role": "viewer", "senha": a.h("velha"), "primeiro_acesso": False}], open(self.b, "w"))
        a.aplicar_usuarios([reg])
        self.assertIsNotNone(a.verificar_login("ana", "nova123")); self.assertIsNone(a.verificar_login("ana", "velha"))
        a.aplicar_usuarios([{**reg, "dados": json.dumps({**dados, "senha": a.h("x")}), "atualizado_em": "2000-01-01T00:00:00.000Z"}])
        self.assertIsNotNone(a.verificar_login("ana", "nova123"))        # registro mais velho nao desfaz a troca
    def test_sem_permissao_grava_copia_local(self):
        real = open
        def negar(p, *k, **kw):
            if p == self.b and "w" in (k[0] if k else kw.get("mode", "r")): raise PermissionError()
            return real(p, *k, **kw)
        with mock.patch("builtins.open", negar), mock.patch("shared_store.enviar_async"):
            a.atualizar_email_senha("ana", "a@x.com", "nova123")
        self.assertTrue(os.path.exists(self.l)); self.assertIsNotNone(a.verificar_login("ana", "nova123"))
if __name__ == "__main__":
    unittest.main()
