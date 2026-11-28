"""Pruebas de escudos y logos automáticos con respuestas falsas de Wikipedia (sin red)."""
import io
import json
import tempfile
import unittest
import urllib.parse
from datetime import datetime
from pathlib import Path

import escudos_auto as ea
from agenda_ia import TZ_BO

AHORA = datetime(2026, 11, 27, 12, tzinfo=TZ_BO)
IMAGEN = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/x.svg/320px-x.svg.png"


def png():
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGBA", (400, 400), "red").save(b, "PNG")
    return b.getvalue()


def pagina(titulo, descripcion="club de fútbol de España", extracto="", imagen=IMAGEN):
    return {"title": titulo, "description": descripcion, "extract": extracto or f"{titulo} es un club de fútbol.",
            **({"thumbnail": {"source": imagen}} if imagen else {})}


class Falso:
    """Wikipedia de mentira: la búsqueda devuelve todas las páginas, en orden."""

    def __init__(self, paginas):
        self.paginas, self.pedidos = paginas, []

    def __call__(self, url):
        self.pedidos.append(url)
        if "list=search" in url:
            return {"query": {"search": [{"title": t} for t in self.paginas]}}
        titulo = urllib.parse.unquote(url.rsplit("/", 1)[1]).replace("_", " ")
        return self.paginas.get(titulo, {})


class Base(unittest.TestCase):
    def setUp(self):
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        self.salida = Path(carpeta.name)


def partido(local, visitante=""):
    return {"local": local, "visitante": visitante}


class Escudos(Base):
    def correr(self, eventos, falso, fijos=()):
        return ea.completar(self.salida, "https://x/datos/", eventos, list(fijos), {},
                            pedir=falso, descargar=lambda u: png(), pausa=0, ahora=AHORA)

    def test_baja_el_escudo_y_no_lo_vuelve_a_buscar(self):
        falso = Falso({"Real Betis": pagina("Real Betis")})
        [e] = self.correr([partido("Real Betis")], falso)
        self.assertEqual(e["url"], "https://x/datos/escudos/auto_real-betis.png")
        self.assertTrue((self.salida / "escudos" / "auto_real-betis.png").exists())
        antes = len(falso.pedidos)
        self.correr([partido("Real Betis")], falso)
        self.assertEqual(len(falso.pedidos), antes)

    def test_el_nombre_corto_encuentra_la_pagina_del_club(self):
        falso = Falso({"Club Bolívar": pagina("Club Bolívar", "club de fútbol de Bolivia")})
        [e] = self.correr([partido("Bolívar")], falso)
        self.assertIn("Bolívar", e["nombres"])

    def test_los_escudos_a_mano_mandan(self):
        falso = Falso({})
        self.assertEqual(self.correr([partido("Bolívar")], falso, [{"nombres": ["Bolívar"], "url": "u"}]), [])
        self.assertEqual(falso.pedidos, [])

    def test_no_acepta_otro_deporte_otro_nombre_ni_pagina_sin_imagen(self):
        falso = Falso({
            "Betis Baloncesto": pagina("Betis Baloncesto", "club de baloncesto", "Es un club de baloncesto."),
            "Sevilla FC": pagina("Sevilla FC"),
            "Betis": pagina("Betis", imagen=None),
        })
        self.assertEqual(self.correr([partido("Betis")], falso), [])

    def test_no_encontrado_se_recuerda_un_tiempo(self):
        falso = Falso({})
        self.correr([partido("Equipo Raro")], falso)
        antes = len(falso.pedidos)
        self.correr([partido("Equipo Raro")], falso)
        self.assertEqual(len(falso.pedidos), antes)
        datos = json.loads((self.salida / "escudos_auto.json").read_text(encoding="utf-8"))
        self.assertIn("equipo raro", datos["sin_escudo"])

    def test_un_error_de_red_no_marca_el_equipo_como_inexistente(self):
        def falla(url):
            raise ConnectionError("sin red")
        ea.completar(self.salida, "u", [partido("Real Betis")], [], {}, pedir=falla, pausa=0, ahora=AHORA)
        self.assertEqual(json.loads((self.salida / "escudos_auto.json").read_text(encoding="utf-8"))["sin_escudo"], {})

    def test_solo_acepta_imagenes_de_wikimedia(self):
        falso = Falso({"Real Betis": pagina("Real Betis", imagen="https://malo.example.com/x.png")})
        self.assertEqual(self.correr([partido("Real Betis")], falso), [])


class Torneos(Base):
    def correr(self, competencias, falso):
        return ea.completar_torneos(self.salida, "https://x/datos/", [{"competencia": c} for c in competencias], {},
                                    pedir=falso, descargar=lambda u: png(), pausa=0, ahora=AHORA,
                                    fijos=self.salida / "nada")

    LALIGA = pagina("Primera División de España", "liga de fútbol de España",
                    "La Primera División de España, conocida como LaLiga EA Sports por patrocinio, es la máxima categoría.")

    def test_encuentra_el_logo_aunque_la_pagina_tenga_otro_nombre(self):
        falso = Falso({"Primera División de España": self.LALIGA})
        [t] = self.correr(["LaLiga EA Sports"], falso)
        self.assertEqual(t["url"], "https://x/datos/escudos/torneo_primera-division-de-espana.png")
        antes = len(falso.pedidos)
        self.correr(["LaLiga EA Sports"], falso)
        self.assertEqual(len(falso.pedidos), antes)

    def test_un_nombre_demasiado_generico_no_se_adivina(self):
        falso = Falso({"Liga Nacional de Fútbol Americano": pagina("Liga Nacional de Fútbol Americano")})
        self.assertEqual(self.correr(["Liga"], falso), [])

    def test_los_logos_a_mano_mandan(self):
        fijos = self.salida / "fijos"
        fijos.mkdir()
        (fijos / "champions.png").write_bytes(png())
        (fijos / "torneos.json").write_text(json.dumps([{"archivo": "champions.png", "nombres": ["Champions League"]}]))
        lista = ea.completar_torneos(self.salida, "https://x/", [{"competencia": "UEFA Champions League"}], {},
                                     pedir=lambda u: self.fail("no debe buscar"), pausa=0, fijos=fijos)
        self.assertEqual(lista[0]["url"], "https://x/escudos/champions.png")


if __name__ == "__main__":
    unittest.main()
