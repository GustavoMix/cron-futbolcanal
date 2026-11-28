"""Escudos de equipos y logos de torneos, buscados solos en Wikipedia.

Los escudos puestos a mano en `escudos/` siempre mandan. Para cada equipo o torneo de la agenda
sin imagen se busca una sola vez: el resultado (también el "no se encontró") queda en
`escudos_auto.json` y `torneos_auto.json` dentro de los datos, así no se repite el pedido. La
imagen es la principal de la página de Wikipedia. Solo se acepta una página cuyo nombre se
parezca al buscado y, en equipos, que hable de fútbol: sin escudo es mejor que uno equivocado.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

import agenda_ia

TZ_BO = timezone(timedelta(hours=-4))
MAXIMO_POR_CORRIDA = 15
PAUSA_SEG = 1.0            # Wikipedia pide no martillar el servicio
REINTENTAR_DIAS = 14       # un equipo no encontrado se vuelve a buscar pasado este tiempo
LADO_PX = 160


def _pedir_json(url: str) -> dict:
    pedido = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(pedido, timeout=30) as r:
        return json.load(r)


def _descargar(url: str) -> bytes:
    pedido = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(pedido, timeout=30) as r:
        return r.read(3_000_000)


def _como_png(crudo: bytes) -> bytes:
    """Reduce el escudo a un tamaño liviano; si no hay Pillow lo deja como vino."""
    try:
        from PIL import Image
    except ImportError:
        return crudo
    img = Image.open(io.BytesIO(crudo)).convert("RGBA")
    img.thumbnail((LADO_PX, LADO_PX))
    salida = io.BytesIO()
    img.save(salida, "PNG", optimize=True)
    return salida.getvalue()


IDIOMAS = ("es", "en")
SUFIJO_EQUIPO = {"es": " club de fútbol", "en": " football club"}
ES_FUTBOL = re.compile(r"f[uú]tbol|football|soccer", re.I)
MAXIMO_CANDIDATOS = 3


def _compacto(texto: str) -> str:
    return agenda_ia.clave_equipo(texto).replace(" ", "")


def _coincide(nombre: str, resumen: dict, minimo: int = 4) -> bool:
    """¿La página trata de lo que se buscó? Por el título (incluye redirecciones) o por su inicio."""
    buscado, titulo = _compacto(nombre), _compacto(resumen.get("title") or "")
    if not buscado or not titulo:
        return False
    if buscado == titulo or (min(len(buscado), len(titulo)) >= minimo and (buscado in titulo or titulo in buscado)):
        return True
    if SequenceMatcher(None, buscado, titulo).ratio() >= 0.8:
        return True
    return len(buscado) >= 6 and buscado in _compacto((resumen.get("extract") or "")[:250])


def _imagen(resumen: dict) -> str | None:
    url = (resumen.get("thumbnail") or {}).get("source") or ""
    host = urllib.parse.urlparse(url).hostname or ""
    return url if url.startswith("https://") and host.endswith("wikimedia.org") else None


def _buscar_wiki(pedir, nombre: str, equipo: bool, pausa: float = 0.0) -> dict | None:
    """La página de Wikipedia que mejor responde al nombre, con su imagen principal, o None."""
    for idioma in IDIOMAS:
        consulta = nombre + (SUFIJO_EQUIPO[idioma] if equipo else "")
        base = f"https://{idioma}.wikipedia.org"
        titulos = pedir(f"{base}/w/api.php?" + urllib.parse.urlencode({
            "action": "query", "list": "search", "srsearch": consulta, "srlimit": 6, "srprop": "", "format": "json",
        })).get("query", {}).get("search", [])
        for resultado in titulos[:MAXIMO_CANDIDATOS]:
            if pausa:
                time.sleep(pausa)
            titulo = resultado.get("title") or ""
            resumen = pedir(f"{base}/api/rest_v1/page/summary/" + urllib.parse.quote(titulo.replace(" ", "_"), safe=""))
            imagen = _imagen(resumen)
            if not imagen or not _coincide(nombre, resumen, 4 if equipo else 6):
                continue
            if equipo and not ES_FUTBOL.search(f"{resumen.get('description') or ''} {(resumen.get('extract') or '')[:300]}"):
                continue
            return {"nombre": resumen.get("title") or titulo, "imagen": imagen}
    return None


def buscar(pedir, nombre: str, pausa: float = 0.0) -> dict | None:
    """El club de fútbol que mejor responde al nombre, con su escudo, o None."""
    hallado = _buscar_wiki(pedir, nombre, equipo=True, pausa=pausa)
    return {"equipo": hallado["nombre"], "alternos": [], "escudo": hallado["imagen"]} if hallado else None


def _espacio(nombre: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", agenda_ia.clave_equipo(nombre)).strip("-") or "equipo"


def completar(salida: Path, url_base: str, eventos: list[dict], fijos: list[dict], cfg: dict,
              pedir=None, descargar=None, pausa: float | None = None, ahora: datetime | None = None) -> list[dict]:
    """Escudos buscados solos: los ya guardados más los que falten de la agenda de hoy."""
    ajustes = cfg.get("escudos", {})
    if not ajustes.get("automaticos", True):
        return []
    pedir, descargar = pedir or _pedir_json, descargar or _descargar
    pausa = PAUSA_SEG if pausa is None else pausa
    ahora = ahora or datetime.now(TZ_BO)
    ruta = salida / "escudos_auto.json"
    cache = json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}
    equipos = [e for e in cache.get("equipos", []) if (salida / "escudos" / e["archivo"]).exists()]
    sin_escudo = cache.get("sin_escudo", {})

    def tiene(nombre: str) -> bool:
        return any(agenda_ia.mismo_equipo(nombre, n) for e in [*fijos, *equipos] for n in e["nombres"])

    def reciente(nombre: str) -> bool:
        visto = sin_escudo.get(agenda_ia.clave_equipo(nombre))
        return bool(visto) and ahora - datetime.fromisoformat(visto) < timedelta(days=REINTENTAR_DIAS)

    nombres = sorted({n for e in eventos for n in (e["local"], e["visitante"]) if n})
    faltan = [n for n in nombres if not tiene(n) and not reciente(n)]
    buscados = nuevos = 0
    for nombre in faltan[:ajustes.get("max_por_corrida", MAXIMO_POR_CORRIDA)]:
        if buscados:
            time.sleep(pausa)
        buscados += 1
        try:
            hallado = buscar(pedir, nombre, pausa)
            if hallado:
                archivo = f"auto_{_espacio(hallado['equipo'])}.png"
                crudo = descargar(hallado["escudo"])
        except Exception as e:  # sin red o cupo agotado: se sigue otro día, sin marcar "no existe"
            print(f"  escudo de {nombre}: {type(e).__name__}: {e}"[:300])
            break
        if hallado:
            try:
                destino = salida / "escudos" / archivo
                destino.parent.mkdir(parents=True, exist_ok=True)
                destino.write_bytes(_como_png(crudo))
            except Exception as e:  # imagen dañada: se salta este equipo
                print(f"  escudo de {nombre} ilegible: {type(e).__name__}: {e}"[:300])
                continue
        if hallado:
            nombres_equipo = list(dict.fromkeys([nombre, hallado["equipo"], *hallado["alternos"][:4]]))
            equipos.append({"nombres": nombres_equipo, "archivo": archivo})
            nuevos += 1
        else:
            sin_escudo[agenda_ia.clave_equipo(nombre)] = ahora.isoformat(timespec="seconds")
    ruta.write_text(json.dumps({"equipos": equipos, "sin_escudo": sin_escudo}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    print(f"Escudos automáticos: {nuevos} nuevos de {buscados} buscados; "
          f"{max(len(faltan) - buscados, 0)} pendientes; {len(equipos)} en total")
    return [{"nombres": e["nombres"], "url": f"{url_base}escudos/{e['archivo']}"} for e in equipos]


# ---------- logos de torneos ----------
def _parecido(nombre: str, otro: str) -> float:
    """Qué tanto se parecen dos nombres de torneo (0 si no se parecen lo bastante)."""
    a, b = _compacto(nombre), _compacto(otro)
    if not a or not b:
        return 0.0
    razon = SequenceMatcher(None, a, b).ratio()
    if a == b or (min(len(a), len(b)) >= 6 and (a in b or b in a)):
        return max(razon, 0.9)
    return razon if razon >= 0.85 else 0.0


def buscar_torneo(pedir, nombre: str, pausa: float = 0.0) -> dict | None:
    """El torneo que mejor responde al nombre, con su logo, o None."""
    hallado = _buscar_wiki(pedir, nombre, equipo=False, pausa=pausa)
    return {"torneo": hallado["nombre"], "alternos": [], "logo": hallado["imagen"]} if hallado else None


def completar_torneos(salida: Path, url_base: str, eventos: list[dict], cfg: dict, pedir=None,
                      descargar=None, pausa: float | None = None, ahora: datetime | None = None,
                      fijos: Path = agenda_ia.RAIZ / "escudos") -> list[dict]:
    """Logos de los torneos de la agenda: los puestos a mano en `escudos/torneos.json` y los buscados solos."""
    ajustes = cfg.get("escudos", {})
    manuales = []
    indice = fijos / "torneos.json"
    if indice.exists():
        destino = salida / "escudos"
        destino.mkdir(parents=True, exist_ok=True)
        manuales = json.loads(indice.read_text(encoding="utf-8"))
        for t in manuales:
            shutil.copyfile(fijos / t["archivo"], destino / t["archivo"])
    resultado = [{"nombres": t["nombres"], "url": f"{url_base}escudos/{t['archivo']}"} for t in manuales]
    if not ajustes.get("automaticos", True):
        return resultado
    pedir, descargar = pedir or _pedir_json, descargar or _descargar
    pausa = PAUSA_SEG if pausa is None else pausa
    ahora = ahora or datetime.now(TZ_BO)
    ruta = salida / "torneos_auto.json"
    cache = json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}
    torneos = [t for t in cache.get("torneos", []) if (salida / "escudos" / t["archivo"]).exists()]
    sin_logo = cache.get("sin_logo", {})

    def tiene(nombre: str) -> bool:
        return any(_parecido(nombre, n) for t in [*manuales, *torneos] for n in t["nombres"])

    def reciente(nombre: str) -> bool:
        visto = sin_logo.get(agenda_ia.clave_equipo(nombre))
        return bool(visto) and ahora - datetime.fromisoformat(visto) < timedelta(days=REINTENTAR_DIAS)

    faltan = [n for n in sorted({e["competencia"] for e in eventos if e.get("competencia")})
              if not tiene(n) and not reciente(n)]
    nuevos = 0
    for i, nombre in enumerate(faltan[:ajustes.get("max_torneos_por_corrida", 8)]):
        if i:
            time.sleep(pausa)
        try:
            hallado = buscar_torneo(pedir, nombre, pausa)
            crudo = descargar(hallado["logo"]) if hallado else None
        except Exception as e:
            print(f"  logo de {nombre}: {type(e).__name__}: {e}"[:300])
            break
        if not hallado:
            sin_logo[agenda_ia.clave_equipo(nombre)] = ahora.isoformat(timespec="seconds")
            continue
        archivo = f"torneo_{_espacio(hallado['torneo'])}.png"
        try:
            (salida / "escudos").mkdir(parents=True, exist_ok=True)
            (salida / "escudos" / archivo).write_bytes(_como_png(crudo))
        except Exception as e:
            print(f"  logo de {nombre} ilegible: {type(e).__name__}: {e}"[:300])
            continue
        torneos.append({"nombres": list(dict.fromkeys([nombre, hallado["torneo"], *hallado["alternos"][:4]])),
                        "archivo": archivo})
        nuevos += 1
    ruta.write_text(json.dumps({"torneos": torneos, "sin_logo": sin_logo}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    print(f"Logos de torneos: {nuevos} nuevos; {len(torneos)} en total")
    return resultado + [{"nombres": t["nombres"], "url": f"{url_base}escudos/{t['archivo']}"} for t in torneos]
