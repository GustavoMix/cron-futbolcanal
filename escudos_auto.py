"""Escudos de los equipos que salen en la agenda, buscados solos en TheSportsDB (gratis).

Los escudos puestos a mano en `escudos/` siempre mandan. Para cada equipo de la agenda que
no tenga escudo se busca una sola vez; el resultado (también el "no se encontró") queda en
`escudos_auto.json` dentro de los datos, así no se repite el pedido. Solo se acepta un
resultado de fútbol cuyo nombre se parezca al buscado: sin escudo es mejor que uno equivocado.
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
API = "https://www.thesportsdb.com/api/v1/json/{clave}/searchteams.php?t={nombre}"
MAXIMO_POR_CORRIDA = 15
PAUSA_SEG = 2.5            # la clave gratuita limita los pedidos por minuto
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


def buscar(pedir, clave: str, nombre: str) -> dict | None:
    """El equipo de fútbol que mejor responde al nombre, con su escudo, o None."""
    datos = pedir(API.format(clave=clave, nombre=urllib.parse.quote(nombre)))
    for e in datos.get("teams") or []:
        if e.get("strSport") != "Soccer":
            continue
        escudo = e.get("strBadge") or e.get("strTeamBadge") or ""
        host = urllib.parse.urlparse(escudo).hostname or ""
        if not escudo.startswith("https://") or not host.endswith("thesportsdb.com"):
            continue
        alternos = [a.strip() for a in (e.get("strAlternate") or "").split(",") if a.strip()]
        if any(agenda_ia.mismo_equipo(nombre, n) for n in [e.get("strTeam") or "", *alternos]):
            return {"equipo": e["strTeam"], "alternos": alternos, "escudo": escudo}
    return None


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
    clave = os.environ.get("THESPORTSDB_KEY", "").strip() or "3"  # "3" es la clave pública gratuita
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
            hallado = buscar(pedir, clave, nombre)
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
URL_LIGAS = "https://www.thesportsdb.com/api/v1/json/{clave}/all_leagues.php"
URL_LIGA = "https://www.thesportsdb.com/api/v1/json/{clave}/lookupleague.php?id={id}"


def _parecido(nombre: str, otro: str) -> float:
    """Qué tanto se parecen dos nombres de torneo (0 si no se parecen lo bastante)."""
    a, b = (agenda_ia.clave_equipo(x).replace(" ", "") for x in (nombre, otro))
    if not a or not b:
        return 0.0
    razon = SequenceMatcher(None, a, b).ratio()
    if a == b or (min(len(a), len(b)) >= 6 and (a in b or b in a)):
        return max(razon, 0.9)
    return razon if razon >= 0.85 else 0.0


def buscar_torneo(pedir, clave: str, nombre: str, ligas: list[dict]) -> dict | None:
    """El torneo que mejor responde al nombre, con su logo, o None."""
    mejor, puntos = None, 0.0
    for liga in ligas:
        nombres = [liga.get("strLeague") or "", *(liga.get("strLeagueAlternate") or "").split(",")]
        p = max((_parecido(nombre, n.strip()) for n in nombres if n.strip()), default=0.0)
        if p > puntos:
            mejor, puntos = liga, p
    if not mejor:
        return None
    datos = (pedir(URL_LIGA.format(clave=clave, id=mejor["idLeague"])).get("leagues") or [{}])[0]
    for campo in ("strBadge", "strLogo"):
        logo = datos.get(campo) or ""
        host = urllib.parse.urlparse(logo).hostname or ""
        if logo.startswith("https://") and host.endswith("thesportsdb.com"):
            alternos = [a.strip() for a in (mejor.get("strLeagueAlternate") or "").split(",") if a.strip()]
            return {"torneo": mejor["strLeague"], "alternos": alternos, "logo": logo}
    return None


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
    clave = os.environ.get("THESPORTSDB_KEY", "").strip() or "3"
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
    if faltan:
        try:
            ligas = [l for l in (pedir(URL_LIGAS.format(clave=clave)).get("leagues") or []) if l.get("idLeague")]
        except Exception as e:
            print(f"  lista de torneos: {type(e).__name__}: {e}"[:300])
            ligas = None
        for i, nombre in enumerate(faltan[:ajustes.get("max_torneos_por_corrida", 8)] if ligas is not None else []):
            if i:
                time.sleep(pausa)
            try:
                hallado = buscar_torneo(pedir, clave, nombre, ligas)
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
