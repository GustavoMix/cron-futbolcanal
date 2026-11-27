"""Pruebas sin llamar a la IA: se usan respuestas falsas."""
import io
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime
from pathlib import Path

import agenda_ia as ia


def bo(dia, hora=0, minuto=0):
    return datetime(2026, 11, dia, hora, minuto, tzinfo=ia.TZ_BO)


def evento(fecha="2026-11-29", hora="17:15", local="Bolívar", visitante="Always Ready"):
    return {"fecha": fecha, "hora": hora, "local": local, "visitante": visitante,
            "titulo": "", "competencia": "Liga", "canal": ""}


class Falso:
    def __init__(self, eventos=(), error=None, respuesta=None):
        self.pedidos, self.error = [], error
        self.respuesta = respuesta or {"status": "completed", "outputs": [
            {"type": "text", "text": json.dumps({"eventos": list(eventos)})}]}

    def __call__(self, cuerpo):
        self.pedidos.append(cuerpo)
        if self.error:
            raise self.error
        return self.respuesta


class Base(unittest.TestCase):
    def setUp(self):
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        self.salida = Path(carpeta.name)
        (self.salida / "media").mkdir()
        (self.salida / "media" / "a.jpg").write_bytes(b"imagen")

    def pub(self, texto="Fixture de la fecha", dia=26, id="p1", imagenes=1):
        return {"id": id, "fuente": "entelgol", "texto": texto, "fecha_unix": int(bo(dia, 1).timestamp()),
                "imagenes": [{"archivo": "media/a.jpg", "texto_ocr": ""} for _ in range(imagenes)]}

    def leer(self, falso, publicaciones):
        ia.leer_afiches(self.salida, publicaciones, {"ia": {"pausa_seg": 0}}, pedir=falso,
                        ahora=bo(27, 15).timestamp())


class Afiches(Base):
    def test_guarda_eventos_y_no_repite(self):
        p = self.pub()
        falso = Falso([evento()])
        self.leer(falso, [p])
        self.leer(falso, [p])
        self.assertEqual(len(falso.pedidos), 1)
        [e] = ia.eventos_del_feed([p])
        self.assertEqual(e["titulo"], "Bolívar vs Always Ready")
        self.assertEqual(e["inicio_unix"], int(bo(29, 17, 15).timestamp()))

    def test_salta_imagenes_viejas_o_sin_anuncio(self):
        falso = Falso()
        self.leer(falso, [self.pub(dia=1), self.pub(texto="Feliz día a todos")])
        self.assertEqual(falso.pedidos, [])

    def test_los_calendarios_van_primero(self):
        suelto = self.pub(texto="Partido hoy 19:00", dia=27, id="nuevo")
        calendario = self.pub(dia=25, id="calendario")
        cola = ia.pendientes([suelto, calendario], bo(27, 15).timestamp(), 7)
        self.assertEqual([p["id"] for p, _ in cola], ["calendario", "nuevo"])

    def test_si_falla_o_no_se_entiende_queda_pendiente(self):
        for falso in (Falso(error=ConnectionError("sin red")), Falso(respuesta={"status": "failed"})):
            p = self.pub()
            self.leer(falso, [p])
            self.assertNotIn("ia_eventos", p["imagenes"][0])

    def test_con_el_cupo_agotado_se_detiene(self):
        error = urllib.error.HTTPError("u", 429, "cupo", {}, io.BytesIO())
        falso = Falso(error=error)
        self.leer(falso, [self.pub(imagenes=5)])
        self.assertEqual(len(falso.pedidos), 1)

    def test_tras_tres_fallos_seguidos_se_detiene(self):
        falso = Falso(error=ConnectionError("sin red"))
        self.leer(falso, [self.pub(imagenes=5)])
        self.assertEqual(len(falso.pedidos), 3)

    def test_formatos_de_fecha_y_canal(self):
        p = self.pub()
        p["imagenes"][0]["ia_eventos"] = [{**evento(fecha="29/11/26"), "canal": "Canal 61"},
                                          evento(fecha="28/11", local="Aurora", visitante="Blooming")]
        eventos = ia.eventos_del_feed([p])
        self.assertEqual([e["fecha"][:10] for e in eventos], ["2026-11-28", "2026-11-29"])
        self.assertEqual(eventos[1]["canal"], "61")

    def test_con_dia_pero_sin_hora(self):
        p = self.pub()
        p["imagenes"][0]["ia_eventos"] = [evento(hora="")]
        [e] = ia.eventos_del_feed([p])
        self.assertFalse(e["hora_confirmada"])
        self.assertEqual(e["inicio_unix"], int(bo(29).timestamp()))

    def test_la_hora_sale_del_fixture(self):
        fixture = self.pub(id="fixture")
        fixture["imagenes"][0]["ia_eventos"] = [evento()]
        programa = self.pub(texto="¿Bolívar vs Always Ready, la final adelantada?", id="programa")
        analisis = {**evento(hora="", local="", visitante=""), "titulo": "Galácticos: Bolívar vs Always Ready"}
        otro = evento(hora="", local="Aurora", visitante="The Strongest")
        programa["imagenes"][0]["ia_eventos"] = [analisis, otro]
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

    def test_publica_los_guardados(self):
        fijos = self.salida / "fijos"
        fijos.mkdir()
        (fijos / "bolivar.png").write_bytes(b"png")
        (fijos / "equipos.json").write_text(
            json.dumps([{"archivo": "bolivar.png", "nombres": ["Bolívar"]}]), encoding="utf-8")
        [bolivar] = ia.publicar_escudos(self.salida, "https://x/datos/", fijos)
        self.assertEqual(bolivar["url"], "https://x/datos/escudos/bolivar.png")
        self.assertTrue((self.salida / "escudos" / "bolivar.png").exists())

    def test_sin_escudos_no_falla(self):
        self.assertEqual(ia.publicar_escudos(self.salida, "https://x/", self.salida / "no-existe"), [])


if __name__ == "__main__":
    unittest.main()
