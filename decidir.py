"""Decide si en este turno vale la pena leer Facebook.

El workflow se despierta cada 30 minutos y este programa mira el feed.json de la
última lectura: seguido cuando hay partido, cada pocas horas cuando hay calma.
Solo usa la biblioteca estándar para que responda en segundos.
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

TZ_BO = timezone(timedelta(hours=-4))

ANTES_DEL_PARTIDO = timedelta(minutes=30)
DESPUES_DEL_PARTIDO = timedelta(minutes=150)  # desde la hora de inicio
VIVO_VIGENTE = timedelta(hours=6)
NOVEDADES_HORAS = 3
NOVEDADES_MINIMO = 15

# Minutos que deben haber pasado desde la última lectura. Van un poco por debajo
# del objetivo porque GitHub no despierta el workflow con puntualidad.
CADA_PARTIDO = 25       # ~30 min
CADA_FALLO = 55         # ~1 h
CADA_MOVIDO = 115       # ~2 h
CADA_CALMA = 350        # ~6 h

DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]

ES_PARTIDO = re.compile(
    r"\bvs\b|partid|\bfinal|semifinal|\bjueg|enfrent|clasico|\bcancha\b|champions|"
    r"\bliga\b|\bcopa\b|eliminatoria|seleccion|\bgol\b"
)
HORA = re.compile(
    r"(?<![\d:])([01]?\d|2[0-3])(?::|h)([0-5]\d)(?![\d:])"
    r"|(?<![\d:])([01]?\d|2[0-3])\s*(?:hrs?\b|horas\b)"
)


def sin_tildes(texto: str) -> str:
    # NFKC también convierte las "letras bonitas" (𝗩𝗜𝗘𝗥𝗡𝗘𝗦) en letras normales.
    return unicodedata.normalize("NFKC", texto).lower().translate(str.maketrans("áéíóú", "aeiou"))


def _dia_del_mes(dia: int, mes: int | None, publicado: date) -> date | None:
    anio = publicado.year
    if mes is None:
        mes = publicado.month
        if dia < publicado.day - 15:  # "sábado 2" publicado a fin de mes
            mes += 1
    elif mes < publicado.month - 6:
        anio += 1
    if mes > 12:
        mes, anio = 1, anio + 1
    try:
        return date(anio, mes, dia)
    except ValueError:
        return None


def fecha_en_linea(linea: str, publicado: date) -> date | None:
    """Fecha mencionada en una línea (ya sin tildes y en minúsculas)."""
    m = re.search(r"\b(\d{1,2})\s+de\s+(" + "|".join(MESES) + r")\b", linea)
    if m:
        return _dia_del_mes(int(m.group(1)), MESES.index(m.group(2)) + 1, publicado)
    m = re.search(r"\b(" + "|".join(DIAS) + r")\s+(\d{1,2})\b(?![:h])", linea)
    if m:
        return _dia_del_mes(int(m.group(2)), None, publicado)
    if re.search(r"\bhoy\b|esta (noche|tarde)", linea):
        return publicado
    if re.search(r"(?<!la )\bmañana\b", linea):
        return publicado + timedelta(days=1)
    m = re.search(r"\b(" + "|".join(DIAS) + r")\b", linea)
    if m:
        return publicado + timedelta(days=(DIAS.index(m.group(1)) - publicado.weekday()) % 7)
    return None


def horas_anunciadas(publicacion: dict) -> list[datetime]:
    """Horas de partido escritas en el texto de una publicación."""
    texto = sin_tildes(publicacion.get("texto") or "")
    if not ES_PARTIDO.search(texto):
        return []
    publicado = datetime.fromtimestamp(publicacion["fecha_unix"], TZ_BO)
    lineas = texto.splitlines()
    fechas = [fecha_en_linea(l, publicado.date()) for l in lineas]
    # Si la hora va antes de cualquier fecha, vale la primera fecha del texto.
    actual = next((f for f in fechas if f), None)
    halladas = []
    for linea, fecha in zip(lineas, fechas):
        actual = fecha or actual
        for m in HORA.finditer(linea):
            hora = int(m.group(1) or m.group(3))
            minuto = int(m.group(2) or 0)
            dia = actual or publicado.date()
            halladas.append(datetime(dia.year, dia.month, dia.day, hora, minuto, tzinfo=TZ_BO))
    return halladas


def decidir(feed: dict, ahora: datetime) -> tuple[bool, str]:
    if not feed.get("actualizado"):
        return True, "no hay lectura anterior"
    ultima = datetime.fromisoformat(feed["actualizado"])
    minutos = (ahora - ultima).total_seconds() / 60
    publicaciones = feed.get("publicaciones", [])

    cada, motivo = CADA_CALMA, "calma"
    anunciadas = [inicio for p in publicaciones for inicio in horas_anunciadas(p)]
    # Las que la IA leyó dentro de los afiches.
    anunciadas += [datetime.fromtimestamp(e["inicio_unix"], TZ_BO) for e in feed.get("eventos", []) if e.get("hora_confirmada", True)]
    partidos = sorted({
        inicio for inicio in anunciadas
        if inicio - ANTES_DEL_PARTIDO <= ahora <= inicio + DESPUES_DEL_PARTIDO
    })
    en_vivo = any(
        (p.get("video") or {}).get("en_vivo")
        and ahora.timestamp() - p["fecha_unix"] <= VIVO_VIGENTE.total_seconds()
        for p in publicaciones
    )
    recientes = sum(
        ultima.timestamp() - p["fecha_unix"] <= NOVEDADES_HORAS * 3600 for p in publicaciones
    )
    if partidos:
        cada = CADA_PARTIDO
        motivo = "partido a las " + ", ".join(f"{i:%H:%M}" for i in partidos)
    elif en_vivo:
        cada, motivo = CADA_PARTIDO, "había una transmisión en vivo"
    elif not all(f.get("ok") for f in feed.get("fuentes", [])):
        cada, motivo = CADA_FALLO, "una página falló la última vez"
    elif recientes >= NOVEDADES_MINIMO:
        cada = CADA_MOVIDO
        motivo = f"mucho movimiento ({recientes} publicaciones en {NOVEDADES_HORAS} h)"

    return minutos >= cada, f"{motivo}; pasaron {minutos:.0f} min y se lee desde los {cada}"


def main() -> int:
    ruta = Path(sys.argv[1] if len(sys.argv) > 1 else "datos/feed.json")
    if os.environ.get("FORZAR") == "true":
        correr, motivo = True, "lanzado a mano"
    else:
        try:
            feed = json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}
            correr, motivo = decidir(feed, datetime.now(TZ_BO))
        except Exception as e:  # ante la duda se lee: peor es dejar de actualizar
            correr, motivo = True, f"no se pudo decidir ({type(e).__name__}: {e})"
    print(f"{'LEER' if correr else 'ESPERAR'}: {motivo}")
    salida = os.environ.get("GITHUB_OUTPUT")
    if salida:
        with open(salida, "a", encoding="utf-8") as f:
            f.write(f"correr={'true' if correr else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
