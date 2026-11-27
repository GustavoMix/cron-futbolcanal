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
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
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
