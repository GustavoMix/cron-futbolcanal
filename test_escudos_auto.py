"""Pruebas de los escudos automáticos con respuestas falsas (sin red)."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import escudos_auto as ea
from agenda_ia import TZ_BO

def png():
    from PIL import Image
    import io
    b = io.BytesIO()
    Image.new("RGBA", (400, 400), "red").save(b, "PNG")
    return b.getvalue()


AHORA = datetime(2026, 11, 27, 12, tzinfo=TZ_BO)


def partido(local, visitante):
    return {"local": local, "visitante": visitante}


def equipo(nombre, deporte="Soccer", escudo="https://r2.thesportsdb.com/images/x.png", alternos=""):
    return {"strTeam": nombre, "strSport": deporte, "strBadge": escudo, "strAlternate": alternos}


class Escudos(unittest.TestCase):
    def setUp(self):
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        self.salida = Path(carpeta.name)
        self.pedidos = []

    def pedir(self, respuestas):
        def f(url):
            self.pedidos.append(url)
            for nombre, teams in respuestas.items():
                if nombre in url:
                    return {"teams": teams}
            return {"teams": None}
        return f

    def correr(self, eventos, respuestas, fijos=()):
        return ea.completar(self.salida, "https://x/datos/", eventos, list(fijos), {},
                            pedir=self.pedir(respuestas), descargar=lambda u: png(), pausa=0, ahora=AHORA)

    def test_baja_el_escudo_y_no_lo_vuelve_a_buscar(self):
        resp = {"Real%20Betis": [equipo("Real Betis")]}
        [e] = self.correr([partido("Real Betis", "")], resp)
        self.assertEqual(e["url"], "https://x/datos/escudos/auto_real-betis.png")
        self.assertTrue((self.salida / "escudos" / "auto_real-betis.png").exists())
        self.correr([partido("Real Betis", "")], resp)
        self.assertEqual(len(self.pedidos), 1)

    def test_los_escudos_a_mano_mandan(self):
        fijos = [{"nombres": ["Bolívar"], "url": "u"}]
        self.assertEqual(self.correr([partido("Bolívar", "")], {}, fijos), [])
        self.assertEqual(self.pedidos, [])

    def test_no_acepta_otro_deporte_ni_otro_equipo(self):
        resp = {"Betis": [equipo("Betis Baloncesto", "Basketball"), equipo("Sevilla")]}
        self.assertEqual(self.correr([partido("Betis", "")], resp), [])

    def test_no_encontrado_se_recuerda_un_tiempo(self):
        self.correr([partido("Equipo Raro", "")], {})
        self.correr([partido("Equipo Raro", "")], {})
        self.assertEqual(len(self.pedidos), 1)
        datos = json.loads((self.salida / "escudos_auto.json").read_text(encoding="utf-8"))
        self.assertIn("equipo raro", datos["sin_escudo"])

    def test_un_error_de_red_no_marca_el_equipo_como_inexistente(self):
        def falla(url):
            raise ConnectionError("sin red")
        ea.completar(self.salida, "u", [partido("Real Betis", "")], [], {}, pedir=falla, pausa=0, ahora=AHORA)
        self.assertEqual(json.loads((self.salida / "escudos_auto.json").read_text(encoding="utf-8"))["sin_escudo"], {})

    def test_solo_acepta_escudos_de_thesportsdb(self):
        resp = {"Betis": [equipo("Real Betis", escudo="https://malo.example.com/x.png")]}
        self.assertEqual(self.correr([partido("Real Betis", "")], resp), [])


if __name__ == "__main__":
    unittest.main()
