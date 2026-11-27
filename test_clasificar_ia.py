"""Pruebas de la clasificación de publicaciones con respuestas falsas."""
import json
import unittest
from datetime import datetime, timedelta, timezone

import clasificar_ia as ci

AHORA = datetime(2026, 11, 27, 12, tzinfo=timezone(timedelta(hours=-4))).timestamp()


def pub(texto, dias=1, **extra):
    return {"texto": texto, "tipo": "foto", "fecha_unix": AHORA - dias * 86400, "imagenes": [], **extra}


def responde(filas):
    return lambda cuerpo: {"outputs": [{"type": "text", "text": json.dumps({"publicaciones": filas})}]}


class Clasificar(unittest.TestCase):
    def test_guarda_la_clase_y_no_repite(self):
        a, b = pub("Bolívar 2-0 Aurora, final"), pub("Sorteo de camisetas")
        pedidos = []

        def pedir(cuerpo):
            pedidos.append(cuerpo)
            return responde([{"n": 1, "clase": "resultado"}, {"n": 2, "clase": "Promocion"}])(cuerpo)
        ci.clasificar([a, b], {}, pedir=pedir, ahora=AHORA)
        ci.clasificar([a, b], {}, pedir=pedir, ahora=AHORA)
        self.assertEqual((a["ia_clase"], b["ia_clase"]), ("resultado", "promocion"))
        self.assertEqual(len(pedidos), 1)

    def test_clase_desconocida_o_numero_fuera_de_rango(self):
        p = pub("algo")
        ci.clasificar([p], {}, pedir=responde([{"n": 1, "clase": "inventada"}, {"n": 9, "clase": "noticia"}]), ahora=AHORA)
        self.assertEqual(p["ia_clase"], "otro")

    def test_si_falla_queda_pendiente(self):
        p = pub("algo")
        ci.clasificar([p], {}, pedir=lambda c: {}, ahora=AHORA)
        self.assertNotIn("ia_clase", p)

    def test_solo_las_recientes(self):
        self.assertEqual([p["texto"] for p in ci.pendientes([pub("vieja", dias=30), pub("nueva")], AHORA)], ["nueva"])
