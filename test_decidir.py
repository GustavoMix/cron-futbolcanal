import unittest
from datetime import datetime, timedelta

from decidir import TZ_BO, decidir, horas_anunciadas


def bo(dia, hora, minuto=0, mes=11):
    return datetime(2026, mes, dia, hora, minuto, tzinfo=TZ_BO)


def pub(texto, publicado, en_vivo=False):
    return {"texto": texto, "fecha_unix": int(publicado.timestamp()),
            "video": {"en_vivo": True} if en_vivo else None}


def feed(ultima, *publicaciones, ok=True):
    return {"actualizado": ultima.isoformat(), "fuentes": [{"ok": ok}],
            "publicaciones": list(publicaciones)}


class Horas(unittest.TestCase):
    def test_hoy(self):
        p = pub("LA VERDE EN SEMIFINALES\n📅 HOY, VIERNES 27\n⏰ 14:00 HRS.", bo(27, 12))
        self.assertEqual(horas_anunciadas(p), [bo(27, 14)])

    def test_fecha_futura_despues_de_la_hora(self):
        p = pub("La final se juega\n🕓 16:00 H (hora boliviana).\n📅 Sábado 28 de noviembre", bo(24, 1))
        self.assertEqual(horas_anunciadas(p), [bo(28, 16)])

    def test_agenda_de_varios_dias(self):
        p = pub("📅 SÁBADO 28\nBetis vs Rayo | 09:00 | Canal 61\n📅 DOMINGO 29\n"
                "Real Madrid vs Alavés | 16:00", bo(27, 11))
        self.assertEqual(horas_anunciadas(p), [bo(28, 9), bo(29, 16)])

    def test_manana(self):
        p = pub("¡MAÑANA se juega el clásico! 19:00", bo(26, 10))
        self.assertEqual(horas_anunciadas(p), [bo(27, 19)])

    def test_cambio_de_mes(self):
        p = pub("Partido el miércoles 2 a las 20 hrs", bo(29, 10))
        self.assertEqual(horas_anunciadas(p), [bo(2, 20, mes=12)])

    def test_sin_fecha_es_el_dia_publicado(self):
        p = pub("El partido cambia. ⏰ Nueva hora: 20:00", bo(27, 13))
        self.assertEqual(horas_anunciadas(p), [bo(27, 20)])

    def test_programa_que_no_es_partido(self):
        p = pub("HOY no te pierdas nuestro programa a partir de las 13:00", bo(27, 10))
        self.assertEqual(horas_anunciadas(p), [])


class Decision(unittest.TestCase):
    partido = pub("Partido HOY 19:00", bo(27, 10))

    def correr(self, ahora, hace_min, *pubs, ok=True):
        return decidir(feed(ahora - timedelta(minutes=hace_min), *pubs, ok=ok), ahora)[0]

    def test_calma_cada_seis_horas(self):
        self.assertFalse(self.correr(bo(27, 12), 300))
        self.assertTrue(self.correr(bo(27, 12), 360))

    def test_partido_cada_media_hora(self):
        for ahora in (bo(27, 18, 30), bo(27, 19, 45), bo(27, 21, 30)):
            self.assertTrue(self.correr(ahora, 30, self.partido), ahora)
        self.assertFalse(self.correr(bo(27, 19), 10, self.partido))

    def test_fuera_del_partido_vuelve_la_calma(self):
        for ahora in (bo(27, 18), bo(27, 22)):
            self.assertFalse(self.correr(ahora, 60, self.partido), ahora)

    def test_en_vivo(self):
        vivo = pub("Transmisión", bo(27, 14), en_vivo=True)
        self.assertTrue(self.correr(bo(27, 15), 30, vivo))
        self.assertFalse(self.correr(bo(28, 3), 30, vivo))  # ya es viejo

    def test_mucho_movimiento_cada_dos_horas(self):
        noticias = [pub("noticia", bo(27, 10))] * 15
        self.assertTrue(self.correr(bo(27, 13), 120, *noticias))
        self.assertFalse(self.correr(bo(27, 13), 60, *noticias))

    def test_pagina_fallida_cada_hora(self):
        self.assertTrue(self.correr(bo(27, 12), 60, ok=False))

    def test_sin_historial(self):
        self.assertTrue(decidir({}, bo(27, 12))[0])


if __name__ == "__main__":
    unittest.main()
