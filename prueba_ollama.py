"""Prueba: ¿un modelo abierto en Ollama lee bien los afiches? Compara con los horarios reales.

No toca el cron ni los datos. Se ejecuta desde el workflow "Probar Ollama".
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
import urllib.request
from datetime import datetime

from PIL import Image

from agenda_ia import ESQUEMA, INSTRUCCIONES, clave_equipo

MODELO = os.environ.get("MODELO", "qwen3-vl:4b")
MEDIA = "https://raw.githubusercontent.com/GustavoMix/cron-futbolcanal/datos/media/"

# (fecha, hora, local, visitante), copiados a mano de cada afiche.
AFICHES = {
    "Liga boliviana, fecha 27": {
        "url": MEDIA + "futbolcanal/f_122187949286653203.jpg",
        "publicado": "2026-11-26 12:12",
        "texto": "Te presentamos el fixture de la jornada 27 de la Liga Boliviana.",
        "reales": [
            ("2026-11-27", "15:00", "ABB", "Real Tomayapo"),
            ("2026-11-27", "19:00", "Independiente", "Real Oruro"),
            ("2026-11-28", "15:00", "San José", "Real Potosí"),
            ("2026-11-28", "17:15", "Guabirá", "Universitario"),
            ("2026-11-28", "19:30", "Potosí", "Petrolero"),
            ("2026-11-29", "15:00", "Aurora", "Strongest"),
            ("2026-11-29", "17:15", "Bolívar", "Always Ready"),
            ("2026-11-29", "19:30", "Blooming", "San Antonio"),
        ],
    },
    "LaLiga, fecha 14": {
        "url": MEDIA + "entelgol/f_122142367508922481.jpg",
        "publicado": "2026-11-26 15:15",
        "texto": "¡Se juega la fecha 14 de LaLiga!",
        "reales": [
            ("2026-11-27", "16:00", "Levante", "Racing"),
            ("2026-11-28", "09:00", "Betis", "Vallecano"),
            ("2026-11-28", "11:15", "Málaga", "Club"),
            ("2026-11-28", "13:30", "Elche", "Madrid"),
            ("2026-11-28", "16:00", "Real Madrid", "Alavés"),
            ("2026-11-29", "09:00", "Valencia", "Osasuna"),
            ("2026-11-29", "11:15", "Deportivo", "Barcelona"),
            ("2026-11-29", "13:30", "Celta", "real"),
            ("2026-11-29", "16:00", "Real Sociedad", "Sevilla"),
            ("2026-11-30", "16:00", "Espanyol", "Getafe"),
        ],
    },
}


def parecido(esperado: str, leido: str) -> bool:
    """Basta que el nombre esperado aparezca dentro del leído ("Vallecano" en "R.Vallecano")."""
    a, b = clave_equipo(esperado).replace(" ", ""), clave_equipo(leido).replace(" ", "")
    return bool(a) and a in b


def leer(afiche: dict) -> list[dict]:
    pedido = urllib.request.Request(afiche["url"], headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(pedido, timeout=60) as r:
        imagen = r.read()
    ancho, alto = Image.open(io.BytesIO(imagen)).size
    publicado = datetime.strptime(afiche["publicado"], "%Y-%m-%d %H:%M")
    esquema = {"type": "object", "properties": {"eventos": ESQUEMA["properties"]["eventos"]},
               "required": ["eventos"]}
    instrucciones = INSTRUCCIONES.split('En "escudos"')[0].format(
        publicado=f"{publicado:%Y-%m-%d %H:%M}", ancho=ancho, alto=alto, texto=afiche["texto"])
    cuerpo = {
        "model": MODELO, "stream": False, "think": False, "format": esquema,
        "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 3000},
        "messages": [{"role": "user", "content": instrucciones + "\nResponde en JSON.",
                      "images": [base64.b64encode(imagen).decode()]}],
    }
    pedido = urllib.request.Request("http://127.0.0.1:11434/api/chat", json.dumps(cuerpo).encode(),
                                    {"Content-Type": "application/json"})
    with urllib.request.urlopen(pedido, timeout=1500) as r:
        respuesta = json.load(r)
    contenido = respuesta["message"]["content"]
    try:
        return json.loads(contenido)["eventos"]
    except (ValueError, KeyError):
        print("Respuesta ilegible:", contenido[:1500])
        return []


def main() -> int:
    aciertos = total = 0
    informe = [f"## Prueba con {MODELO}"]
    for nombre, afiche in AFICHES.items():
        inicio = time.time()
        try:
            eventos = leer(afiche)
        except Exception as e:
            print(f"{nombre}: ERROR {type(e).__name__}: {e}")
            eventos = []
        minutos = (time.time() - inicio) / 60
        print(f"\n=== {nombre}: {len(eventos)} eventos en {minutos:.1f} min")
        print(json.dumps(eventos, ensure_ascii=False))
        bien = 0
        filas = []
        for fecha, hora, local, visitante in afiche["reales"]:
            ok = any(e.get("fecha") == fecha and e.get("hora") == hora
                     and parecido(local, e.get("local", "")) and parecido(visitante, e.get("visitante", ""))
                     for e in eventos)
            bien += ok
            filas.append(f"{'BIEN ' if ok else 'MAL  '} {fecha} {hora}  {local} vs {visitante}")
        resumen = f"{nombre}: {bien} de {len(afiche['reales'])} correctos, {len(eventos)} leídos, {minutos:.1f} min"
        print("\n".join(filas + [resumen]))
        informe.append(f"### {resumen}\n```\n" + "\n".join(filas) + "\n```")
        aciertos += bien
        total += len(afiche["reales"])
    final = f"TOTAL: {aciertos} de {total} partidos correctos"
    print("\n" + final)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("\n\n".join(informe + [f"**{final}**"]) + "\n")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    raise SystemExit(main())
