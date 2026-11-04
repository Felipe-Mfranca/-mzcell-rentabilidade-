"""Painéis de TV (esqueleto) — sem rede."""
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from marketplaces.paineis.router import AREAS, router


class TestPaineis(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.c = TestClient(app, follow_redirects=False)

    def test_todas_as_areas_respondem(self):
        for area in AREAS:
            r = self.c.get(f"/tv/{area}")
            self.assertIn(r.status_code, (200, 307), area)
            if area != "expedicao":
                self.assertIn(AREAS[area][0], r.text)
                self.assertEqual(self.c.get(f"/tv/{area}/dados").json()["area"], area)

    def test_expedicao_aponta_para_o_monitor(self):
        self.assertEqual(self.c.get("/tv/expedicao").headers["location"], "/expedicao/tv")

    def test_area_inexistente_da_404(self):
        self.assertEqual(self.c.get("/tv/nada").status_code, 404)


if __name__ == "__main__":
    unittest.main()
