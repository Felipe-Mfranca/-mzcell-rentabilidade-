"""Atualização pelo servidor: manifesto do git HEAD, lista fechada e troca só com tudo conferido. Sem rede."""
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import atualizador as at


class _Resp:
    def __init__(self, status, content=b""):
        self.status_code, self.content = status, content

    def json(self):
        return json.loads(self.content)


class TestAtualizador(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.srv, self.pc = os.path.join(d.name, "srv"), os.path.join(d.name, "pc")
        os.makedirs(os.path.join(self.srv, "modelos")); os.makedirs(self.pc)
        arquivos = {"index.js": "const ARQUIVOS_ONLINE = [\n  'main.py','dashboard.html'\n];",
                    "arquivos_online.json": json.dumps(["static/app.js", "config.json", "../fora.py"]),
                    "main.py": "print('novo')", "dashboard.html": "<p>novo</p>", "static/app.js": "1",
                    "config.json": '{"segredo": 1}', "atualizador.py": "# at", "version.txt": "1.5.0",
                    "modelos/fontes_atualizacao.json": "{}"}
        for nome, txt in arquivos.items():
            os.makedirs(os.path.dirname(os.path.join(self.srv, nome)), exist_ok=True)
            open(os.path.join(self.srv, nome), "w", encoding="utf-8").write(txt)
        g = lambda *a: subprocess.run(["git", "-C", self.srv, *a], check=True, capture_output=True)
        g("init", "-q"); g("add", "-A"); g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
        open(os.path.join(self.srv, "main.py"), "w").write("print('nao commitado')")   # só o commitado sai
        at._cache.clear()
        open(os.path.join(self.pc, "version.txt"), "w").write("1.4.10")
        open(os.path.join(self.pc, "dashboard.html"), "w", encoding="utf-8").write("<p>novo</p>")   # igual: não baixa

    def _get(self, falhar=None):
        def get(url):
            if "/atualizacao/manifesto" in url:
                return _Resp(200, json.dumps(at.manifesto(self.srv)).encode())
            caminho = url.split("/atualizacao/arquivo/", 1)[1]
            if caminho == falhar:
                return _Resp(200, b"corrompido")
            dados = at.conteudo(self.srv, caminho)
            return _Resp(200, dados) if dados is not None else _Resp(404)
        return get

    def test_manifesto_so_tem_a_lista_e_o_commitado(self):
        m = at.manifesto(self.srv)
        self.assertEqual(m["versao"], "1.5.0")
        self.assertEqual(sorted(m["arquivos"]), ["atualizador.py", "dashboard.html", "main.py", "modelos/fontes_atualizacao.json", "static/app.js"])
        self.assertIsNone(at.conteudo(self.srv, "config.json"))
        self.assertEqual(at.conteudo(self.srv, "main.py"), b"print('novo')")

    def test_pc_baixa_so_o_que_mudou_e_grava_a_versao(self):
        pedidos = []
        get = self._get()
        with mock.patch.object(at, "fontes", return_value=["http://srv"]):
            r = at.atualizar(self.pc, lambda u: (pedidos.append(u), get(u))[1])
        self.assertIn("atualizado de 1.4.10 para 1.5.0", r)
        self.assertEqual(open(os.path.join(self.pc, "main.py")).read(), "print('novo')")
        self.assertEqual(open(os.path.join(self.pc, "static", "app.js")).read(), "1")
        self.assertFalse(any(u.endswith("dashboard.html") for u in pedidos))
        self.assertFalse(os.path.exists(os.path.join(self.pc, "config.json")))
        self.assertEqual(open(os.path.join(self.pc, "version.txt")).read(), "1.5.0")
        with mock.patch.object(at, "fontes", return_value=["http://srv"]):
            self.assertIn("em dia", at.atualizar(self.pc, get))

    def test_arquivo_corrompido_nao_troca_nada(self):
        with mock.patch.object(at, "fontes", return_value=["http://srv"]):
            r = at.atualizar(self.pc, self._get(falhar="static/app.js"))
        self.assertIn("nada trocado", r)
        self.assertFalse(os.path.exists(os.path.join(self.pc, "main.py")))
        self.assertEqual(open(os.path.join(self.pc, "version.txt")).read(), "1.4.10")

    def test_fonte_fora_do_ar_tenta_a_proxima_e_servidor_nao_se_atualiza(self):
        get = self._get()
        def com_queda(u):
            if u.startswith("http://caiu"):
                raise OSError("sem rede")
            return get(u)
        with mock.patch.object(at, "fontes", return_value=["http://caiu", "http://srv"]):
            self.assertIn("por http://srv", at.atualizar(self.pc, com_queda))
        self.assertIn("git", at.atualizar(self.srv, get))
        self.assertTrue(at.versao_maior("1.4.10", "1.4.9"))
        self.assertFalse(at.versao_maior("1.4.9", "1.4.9"))

    def test_fontes_do_mz_local_tem_prioridade(self):
        with mock.patch.dict(os.environ, {"APPDATA": self.pc}):
            self.assertEqual(at.fontes(self.pc), at.FONTES_PADRAO)
            json.dump({"fontes": ["http://reserva:8001/"]}, open(os.path.join(self.pc, "fontes.json"), "w"))
            os.makedirs(os.path.join(self.pc, "modelos"))
            json.dump({"fontes": ["http://reserva:8001/"]}, open(os.path.join(self.pc, "modelos", "fontes_atualizacao.json"), "w"))
            self.assertEqual(at.fontes(self.pc), ["http://reserva:8001"])
            os.makedirs(os.path.join(self.pc, "MZ App"))
            json.dump({"fontes_atualizacao": ["http://outro:8001"]}, open(os.path.join(self.pc, "MZ App", "mz_local.json"), "w"))
            self.assertEqual(at.fontes(self.pc), ["http://outro:8001"])


if __name__ == "__main__":
    unittest.main()
