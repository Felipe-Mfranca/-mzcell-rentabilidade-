"""Bases dos módulos em implantação (Financeiro, Magalu) — sem rede, sem credencial."""
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from marketplaces.financeiro.router import router as financeiro
from marketplaces.magalu.router import router as magalu


class TestBases(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(financeiro)
        app.include_router(magalu)
        self.c = TestClient(app)

    def test_financeiro_estado_e_pagina(self):
        r = self.c.get("/financeiro/estado").json()
        self.assertEqual(r["modulo"], "financeiro")
        self.assertTrue(r["fases"])
        self.assertIn("em implantação", self.c.get("/financeiro").text)

    def test_magalu_estado(self):
        r = self.c.get("/magalu/estado").json()
        self.assertEqual(r["modulo"], "magalu")
        self.assertEqual(r["situacao"], "em_implantacao")
        self.assertNotIn("token", str(r).lower())


if __name__ == "__main__":
    unittest.main()
