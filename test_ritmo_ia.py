"""Pruebas del plan de lectura, con respuestas falsas de la IA."""
import json
import unittest
from datetime import datetime

import ritmo_ia
from decidir import TZ_BO, decidir


def bo(dia, hora, minuto=0):
    return datetime(2026, 11, dia, hora, minuto, tzinfo=TZ_BO)


def pub(fuente, cuando):
    return {"fuente": fuente, "fecha_unix": int(cuando.timestamp())}


def responde(cada, motivo="ritmo bajo"):
    return lambda cuerpo: {"outputs": [{"type": "text", "text": json.dumps({"cada_min": cada, "motivo": motivo})}]}


class Plan(unittest.TestCase):
    ahora = bo(27, 15)
    publicaciones = [pub("a", bo(26, 10)), pub("a", bo(26, 19)), pub("a", bo(27, 9)), pub("b", bo(27, 12))]

    def test_estadisticas_por_pagina(self):
        e = ritmo_ia.estadisticas(self.publicaciones, [], self.ahora)
        self.assertEqual(e["paginas"]["a"]["publicaciones_ultimas_24h"], 2)
        self.assertEqual(e["paginas"]["a"]["minutos_desde_la_ultima"], 360)
        self.assertEqual(e["paginas"]["b"]["mediana_minutos_entre_publicaciones"], None)

    def test_el_plan_se_limita_a_un_rango_razonable(self):
        for pedido, esperado in ((5, 60), (240, 240), (5000, 720)):
            plan = ritmo_ia.planear(self.publicaciones, [], {}, pedir=responde(pedido), ahora=self.ahora)
            self.assertEqual(plan["cada_min"], esperado)

    def test_si_la_ia_falla_no_hay_plan(self):
        def falla(cuerpo):
            raise ConnectionError("sin red")
        self.assertIsNone(ritmo_ia.planear(self.publicaciones, [], {}, pedir=falla, ahora=self.ahora))
        self.assertIsNone(ritmo_ia.planear(self.publicaciones, [], {}, pedir=lambda c: {}, ahora=self.ahora))


class UsoDelPlan(unittest.TestCase):
    def feed(self, plan, ultima):
        return {"actualizado": ultima.isoformat(), "fuentes": [{"ok": True}], "publicaciones": [], "plan": plan}

    def test_respeta_la_espera_que_pidio_la_ia(self):
        plan = {"cada_min": 240, "motivo": "de noche casi no publican"}
        self.assertFalse(decidir(self.feed(plan, bo(27, 10)), bo(27, 12))[0])   # 2 h < 4 h
        self.assertTrue(decidir(self.feed(plan, bo(27, 10)), bo(27, 14, 30))[0])

    def test_sin_plan_rigen_los_valores_fijos(self):
        self.assertFalse(decidir(self.feed(None, bo(27, 10)), bo(27, 14))[0])
        self.assertTrue(decidir(self.feed(None, bo(27, 10)), bo(27, 16, 30))[0])

    def test_un_plan_roto_se_ignora(self):
        self.assertFalse(decidir(self.feed({"cada_min": "x"}, bo(27, 10)), bo(27, 11))[0])


if __name__ == "__main__":
    unittest.main()
