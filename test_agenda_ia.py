"""Pruebas sin llamar a la IA: se usa un cliente falso."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as N

from PIL import Image, ImageDraw

import agenda_ia as ia


def bo(dia, hora=0, minuto=0):
    return datetime(2026, 11, dia, hora, minuto, tzinfo=ia.TZ_BO)


def evento(fecha="2026-11-29", hora="17:15", local="Bolívar", visitante="Always Ready"):
    return {"fecha": fecha, "hora": hora, "local": local, "visitante": visitante,
            "titulo": "", "competencia": "Liga", "canal": ""}


class ClienteFalso:
    def __init__(self, datos=None, error=None, motivo="end_turn"):
        self.pedidos, self.datos, self.error, self.motivo = [], datos, error, motivo
        self.messages = N(create=self._crear)
        self.beta = N(messages=N(create=self._crear))

    def _crear(self, **pedido):
        self.pedidos.append(pedido)
        if self.error:
            raise self.error
        return N(stop_reason=self.motivo, usage=N(input_tokens=10, output_tokens=5),
                 content=[N(type="text", text=json.dumps(self.datos))])


class Base(unittest.TestCase):
    def setUp(self):
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        self.salida = Path(carpeta.name)
        afiche = Image.new("RGB", (600, 600), (80, 30, 140))
        ImageDraw.Draw(afiche).ellipse((100, 100, 220, 220), fill=(250, 210, 0))
        (self.salida / "media").mkdir()
        afiche.save(self.salida / "media" / "a.jpg")

    def pub(self, texto="Fixture de la fecha", dia=26):
        return {"id": "p1", "fuente": "entelgol", "texto": texto, "fecha_unix": int(bo(dia, 1).timestamp()),
                "imagenes": [{"archivo": "media/a.jpg", "texto_ocr": ""}]}

    def leer(self, cliente, publicaciones):
        ia.leer_afiches(self.salida, publicaciones, {}, cliente=cliente, ahora=bo(27, 15).timestamp())


class Afiches(Base):
    def test_guarda_eventos_y_no_repite(self):
        p = self.pub()
        cliente = ClienteFalso({"eventos": [evento()], "escudos": []})
        self.leer(cliente, [p])
        self.leer(cliente, [p])
        self.assertEqual(len(cliente.pedidos), 1)
        [e] = ia.eventos_del_feed([p])
        self.assertEqual(e["titulo"], "Bolívar vs Always Ready")
        self.assertEqual(e["inicio_unix"], int(bo(29, 17, 15).timestamp()))

    def test_salta_imagenes_viejas_o_sin_anuncio(self):
        cliente = ClienteFalso({"eventos": [], "escudos": []})
        self.leer(cliente, [self.pub(dia=1), self.pub(texto="Feliz día a todos")])
        self.assertEqual(cliente.pedidos, [])

    def test_los_calendarios_van_primero(self):
        suelto = {**self.pub(texto="Partido hoy 19:00", dia=27), "id": "nuevo"}
        calendario = {**self.pub(texto="Fixture de la fecha", dia=25), "id": "calendario"}
        cola = ia.pendientes([suelto, calendario], bo(27, 15).timestamp(), 7)
        self.assertEqual([p["id"] for p, _ in cola], ["calendario", "nuevo"])

    def test_si_falla_queda_pendiente(self):
        p = self.pub()
        self.leer(ClienteFalso(error=ConnectionError("sin red")), [p])
        self.assertNotIn("ia_eventos", p["imagenes"][0])

    def test_si_declina_no_se_vuelve_a_pagar(self):
        p = self.pub()
        self.leer(ClienteFalso(motivo="refusal"), [p])
        self.assertEqual(p["imagenes"][0]["ia_eventos"], [])

    def test_con_dia_pero_sin_hora(self):
        p = self.pub()
        p["imagenes"][0]["ia_eventos"] = [evento(hora="")]
        [e] = ia.eventos_del_feed([p])
        self.assertFalse(e["hora_confirmada"])
        self.assertEqual(e["inicio_unix"], int(bo(29).timestamp()))

    def test_la_hora_sale_del_fixture(self):
        fixture = {**self.pub(), "id": "fixture"}
        fixture["imagenes"] = [{"archivo": "media/a.jpg", "ia_eventos": [evento()]}]
        programa = {**self.pub(texto="¿Bolívar vs Always Ready, la final adelantada?"), "id": "programa"}
        analisis = {**evento(hora="", local="", visitante=""), "titulo": "Galácticos: Bolívar vs Always Ready"}
        otro = {**evento(hora="", local="Aurora", visitante="The Strongest")}
        programa["imagenes"] = [{"archivo": "media/a.jpg", "ia_eventos": [analisis, otro]}]
        titulos = [(e["titulo"], e["hora_confirmada"]) for e in ia.eventos_del_feed([fixture, programa])]
        self.assertEqual(titulos, [("Aurora vs The Strongest", False), ("Bolívar vs Always Ready", True)])

    def test_descarta_fechas_imposibles(self):
        p = self.pub()
        p["imagenes"][0]["ia_eventos"] = [evento(fecha="2026-02-30"), evento(fecha="2027-05-01"),
                                          evento(hora="tarde")]
        self.assertEqual(ia.eventos_del_feed([p]), [])


class Escudos(Base):
    def test_nombres(self):
        self.assertEqual(ia.clave_equipo("CDT. Real Oruro"), "real oruro")
        self.assertEqual(ia.clave_equipo("A.B.B."), "abb")

    def test_equipo_nuevo_se_recorta_una_vez(self):
        escudos = [{"equipo": "Equipo Nuevo", "caja": [100, 100, 221, 221]},
                   {"equipo": "Bolívar", "caja": [100, 100, 221, 221]},
                   {"equipo": "Caja Mala", "caja": [500, 500, 900, 900]}]
        imagen = self.salida / "media" / "a.jpg"
        fijos = self.carpeta_fija()
        ia.guardar_escudos_nuevos(self.salida, imagen, escudos, fijos)
        ia.guardar_escudos_nuevos(self.salida, imagen, escudos, fijos)
        guardados = json.loads((self.salida / "escudos" / "ia.json").read_text(encoding="utf-8"))
        self.assertEqual([e["nombres"] for e in guardados], [["Equipo Nuevo"]])
        with Image.open(self.salida / "escudos" / guardados[0]["archivo"]) as png:
            self.assertEqual(png.mode, "RGBA")
            self.assertEqual(png.getpixel((0, 0))[3], 0)  # el fondo quedó transparente

    def carpeta_fija(self) -> Path:
        fijos = self.salida / "fijos"
        fijos.mkdir(exist_ok=True)
        Image.new("RGBA", (10, 10)).save(fijos / "bolivar.png")
        (fijos / "equipos.json").write_text(
            json.dumps([{"archivo": "bolivar.png", "nombres": ["Bolívar"]}]), encoding="utf-8")
        return fijos

    def test_publica_los_fijos(self):
        [bolivar] = ia.publicar_escudos(self.salida, "https://x/datos/", self.carpeta_fija())
        self.assertEqual(bolivar["url"], "https://x/datos/escudos/bolivar.png")
        self.assertTrue((self.salida / "escudos" / "bolivar.png").exists())

    def test_sin_escudos_fijos_no_falla(self):
        self.assertEqual(ia.publicar_escudos(self.salida, "https://x/", self.salida / "no-existe"), [])


if __name__ == "__main__":
    unittest.main()
