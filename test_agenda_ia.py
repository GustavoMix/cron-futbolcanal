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


class TextoYVideos(Base):
    def test_publicacion_sin_imagen_se_lee_por_su_texto(self):
        p = {**self.pub(texto="Bolívar vs Always Ready, sábado 29 a las 17:15"), "imagenes": []}
        falso = Falso([evento()])
        self.leer(falso, [p])
        self.leer(falso, [p])
        self.assertEqual(len(falso.pedidos), 1)
        self.assertEqual([x["type"] for x in falso.pedidos[0]["input"]], ["text"])
        [e] = ia.eventos_del_feed([p])
        self.assertEqual(e["titulo"], "Bolívar vs Always Ready")

    def test_miniatura_de_video_se_lee_como_imagen(self):
        p = {**self.pub(texto="EN VIVO hoy 19:00"), "imagenes": [],
             "video": {"miniatura": {"archivo": "media/a.jpg", "texto_ocr": ""}}}
        falso = Falso([evento(fecha="2026-11-27", hora="19:00")])
        self.leer(falso, [p])
        self.assertEqual([x["type"] for x in falso.pedidos[0]["input"]], ["text", "image"])
        self.assertEqual(len(ia.eventos_del_feed([p])), 1)

    def test_el_mismo_partido_sale_una_sola_vez(self):
        a, b = self.pub(id="a"), self.pub(id="b")
        a["imagenes"][0]["ia_eventos"] = [evento()]
        b["imagenes"][0]["ia_eventos"] = [{**evento(), "canal": "5"}]
        [e] = ia.eventos_del_feed([a, b])
        self.assertEqual(e["canal"], "5")


    def test_nombres_abreviados_no_duplican_el_partido(self):
        a, b, c = self.pub(id="a"), self.pub(id="b"), self.pub(id="c")
        a["imagenes"][0]["ia_eventos"] = [evento(local="Betis", visitante="Rayo Vallecano")]
        b["imagenes"][0]["ia_eventos"] = [evento(local="Betis", visitante="R.Vallecano")]
        c["imagenes"][0]["ia_eventos"] = [evento(local="Rayo Vallecano", visitante="Real Betis")]
        [e] = ia.eventos_del_feed([a, b, c])
        self.assertEqual(e["titulo"], "Real Betis vs Rayo Vallecano")  # conserva el nombre más completo

    def test_dos_partidos_a_la_misma_hora_no_se_mezclan(self):
        p = self.pub()
        p["imagenes"][0]["ia_eventos"] = [evento(local="Betis", visitante="Rayo Vallecano"),
                                          evento(local="Málaga", visitante="Athletic Club")]
        self.assertEqual(len(ia.eventos_del_feed([p])), 2)


    def test_si_cambia_la_hora_vale_el_anuncio_mas_nuevo(self):
        viejo, nuevo = self.pub(id="v", dia=25), self.pub(id="n", dia=27)
        viejo["imagenes"][0]["ia_eventos"] = [evento(hora="17:15")]
        nuevo["imagenes"][0]["ia_eventos"] = [evento(hora="20:00")]
        [e] = ia.eventos_del_feed([viejo, nuevo])
        self.assertEqual(e["fecha"][11:16], "20:00")
        self.assertTrue(e["hora_cambiada"])
        self.assertEqual(e["hora_anterior"], "17:15")

    def test_categoria_y_estadio_se_guardan(self):
        p = self.pub()
        p["imagenes"][0]["ia_eventos"] = [{**evento(), "categoria": "Futbol", "estadio": "Hernando Siles"}]
        [e] = ia.eventos_del_feed([p])
        self.assertEqual((e["categoria"], e["estadio"]), ("futbol", "Hernando Siles"))


    def resultado(self, local, visitante, a, b, **extra):
        return {**evento(hora="", local=local, visitante=visitante), "estado": "finalizado",
                "marcador_local": a, "marcador_visitante": b, **extra}

    def test_el_resultado_final_se_une_al_partido_anunciado(self):
        fixture, final = self.pub(id="f", dia=26), self.pub(id="r", dia=29)
        fixture["imagenes"][0]["ia_eventos"] = [evento(local="Bolívar", visitante="Always Ready")]
        # el afiche del resultado trae los equipos al revés y un nombre abreviado
        final["imagenes"][0]["ia_eventos"] = [self.resultado("Always Ready", "Bolivar", "0", "2")]
        [e] = ia.eventos_del_feed([fixture, final])
        self.assertEqual((e["estado"], e["marcador_local"], e["marcador_visitante"]), ("finalizado", 2, 0))
        self.assertTrue(e["hora_confirmada"])
        self.assertEqual(e["fecha"][11:16], "17:15")

    def test_resultado_sin_partido_anunciado_queda_solo(self):
        final = self.pub(id="r", dia=29)
        final["imagenes"][0]["ia_eventos"] = [self.resultado("Aurora", "Blooming", "1", "1")]
        [e] = ia.eventos_del_feed([final])
        self.assertEqual((e["estado"], e["marcador_local"]), ("finalizado", 1))
        self.assertFalse(e["hora_confirmada"])

    def test_marcador_incompleto_no_cuenta_como_finalizado(self):
        p = self.pub()
        p["imagenes"][0]["ia_eventos"] = [self.resultado("Aurora", "Blooming", "1", "")]
        [e] = ia.eventos_del_feed([p])
        self.assertEqual((e["estado"], e["marcador_local"]), ("programado", None))


class Tablas(Base):
    TABLA = {"competencia": "Liga", "grupo": "", "filas": [
        {"posicion": str(i), "equipo": n, "puntos": str(30 - i), "jugados": "10"}
        for i, n in enumerate(["Bolívar", "Aurora", "Blooming"], 1)]}

    def respuesta(self, tablas):
        return {"outputs": [{"type": "text", "text": json.dumps({"eventos": [], "tablas": tablas})}]}

    def test_lee_y_publica_la_tabla_mas_nueva(self):
        vieja, nueva = self.pub(texto="Tabla de posiciones", dia=25, id="v"), self.pub(texto="Tabla de posiciones", dia=26, id="n")
        otra = {**self.TABLA, "filas": [{**f, "puntos": str(int(f["puntos"]) + 3)} for f in self.TABLA["filas"]]}
        for p, tabla in ((vieja, self.TABLA), (nueva, otra)):
            self.leer(Falso(respuesta=self.respuesta([tabla])), [p])
        [t] = ia.tablas_del_feed([vieja, nueva])
        self.assertEqual(t["publicacion"], "n")
        self.assertEqual([(f["posicion"], f["equipo"], f["puntos"]) for f in t["filas"]][0], (1, "Bolívar", 32))

    def test_filas_invalidas_o_pocas_se_descartan(self):
        p = self.pub()
        p["imagenes"][0]["ia_tablas"] = [{**self.TABLA, "filas": self.TABLA["filas"][:2]}]
        self.assertEqual(ia.tablas_del_feed([p]), [])

    def test_vuelve_a_leer_las_tablas_ya_analizadas_antes_una_sola_vez(self):
        vieja = self.pub(texto="Tabla de posiciones", dia=10)       # leída sin soporte de tablas
        vieja["imagenes"][0]["ia_eventos"] = []
        sin_tabla = self.pub(texto="Feliz día", dia=10, id="x")
        sin_tabla["imagenes"][0]["ia_eventos"] = []
        falso = Falso(respuesta=self.respuesta([self.TABLA]))
        self.leer(falso, [vieja, sin_tabla])
        self.leer(falso, [vieja, sin_tabla])
        self.assertEqual(len(falso.pedidos), 1)
        self.assertEqual(vieja["imagenes"][0]["ia_version"], ia.VERSION_IA)
        self.assertEqual(len(ia.tablas_del_feed([vieja])), 1)


class Nombres(Base):
    def test_un_solo_nombre_por_equipo(self):
        a, b, c = self.pub(id="a"), self.pub(id="b"), self.pub(id="c")
        a["imagenes"][0]["ia_eventos"] = [evento(local="Betis", visitante="Rayo Vallecano")]
        b["imagenes"][0]["ia_eventos"] = [evento(local="Betis", visitante="R.Vallecano", fecha="2026-11-30")]
        c["imagenes"][0]["ia_eventos"] = [evento(local="Real Betis", visitante="Elche", fecha="2026-12-01")]
        canon = ia.canon_de([a, b, c])
        eventos = ia.eventos_del_feed([a, b, c], canon)
        self.assertEqual([e["titulo"] for e in eventos],
                         ["Betis vs Rayo Vallecano", "Betis vs Rayo Vallecano", "Betis vs Elche"])

    def test_manda_el_nombre_de_la_lista_a_mano(self):
        p = self.pub()
        p["imagenes"][0]["ia_eventos"] = [evento(local="ATL. Madrid", visitante="Elche")]
        canon = ia.canon_de([p], [["Atlético de Madrid", "Atletico Madrid", "ATL. Madrid"]])
        self.assertEqual(ia.eventos_del_feed([p], canon)[0]["local"], "Atlético de Madrid")


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
