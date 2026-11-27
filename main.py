"""Actualiza datos/feed.json con las publicaciones de las páginas de paginas.json."""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

from facebook import Navegador, Recolector, caducidad

RAIZ = Path(__file__).parent
TZ_BO = timezone(timedelta(hours=-4))
TESSERACT = shutil.which("tesseract")
URL_BASE = os.environ.get(
    "URL_BASE", "https://raw.githubusercontent.com/GustavoMix/cron-futbolcanal/datos/"
).rstrip("/") + "/"


def iso(unix: float | None = None) -> str:
    return datetime.fromtimestamp(unix or time.time(), TZ_BO).isoformat(timespec="seconds")


def leer_json(ruta: Path, defecto):
    """Si el archivo existe pero está roto, falla: mejor eso que borrar el historial."""
    if not ruta.exists():
        return defecto
    return json.loads(ruta.read_text(encoding="utf-8"))


def guardar_json(ruta: Path, datos):
    temporal = ruta.with_suffix(".tmp")
    temporal.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    temporal.replace(ruta)


# ---------- imágenes ----------
def descargar_imagen(url: str, destino: Path) -> tuple[int, int] | None:
    try:
        pedido = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(pedido, timeout=30) as r:
            crudo = r.read()
        img = Image.open(io.BytesIO(crudo)).convert("RGB")
        img.thumbnail((1080, 1920))
        destino.parent.mkdir(parents=True, exist_ok=True)
        img.save(destino, "JPEG", quality=80, optimize=True)
        return img.size
    except Exception as e:
        print(f"    imagen no descargada: {type(e).__name__}: {e}"[:200])
        return None


def ocr(ruta: Path) -> str:
    """Texto escrito dentro de la imagen (afiches, horarios, marcadores)."""
    if not TESSERACT:
        return ""
    try:
        r = subprocess.run(
            [TESSERACT, str(ruta), "stdout", "-l", "spa", "--psm", "3"],
            capture_output=True, timeout=60, check=True,
        )
    except (subprocess.SubprocessError, OSError) as e:
        print(f"    OCR falló: {e}"[:200])
        return ""
    lineas = []
    for linea in r.stdout.decode("utf-8", "ignore").splitlines():
        linea = " ".join(linea.split())
        # Descarta el ruido: líneas sin ninguna palabra de 3 o más caracteres.
        if re.search(r"[A-Za-zÁÉÍÓÚÑáéíóúñ0-9]{3,}", linea):
            lineas.append(linea)
    return "\n".join(lineas)


def imagen_local(url: str | None, relativa: str, salida: Path, con_ocr: bool = True) -> dict | None:
    """Copia la imagen al repositorio: las URL de Facebook caducan en pocos días."""
    if not url:
        return None
    destino = salida / relativa
    if destino.exists():
        with Image.open(destino) as img:
            tam = img.size
    else:
        tam = descargar_imagen(url, destino)
    if not tam:
        return None
    return {
        "url": URL_BASE + relativa,
        "archivo": relativa,
        "url_original": url,
        "ancho": tam[0],
        "alto": tam[1],
        "texto_ocr": ocr(destino) if con_ocr else "",
    }


# ---------- publicaciones ----------
def titulo_de(texto: str) -> str:
    primera = next((l.strip() for l in (texto or "").splitlines() if l.strip()), "")
    if len(primera) <= 140:
        return primera
    return primera[:140].rsplit(" ", 1)[0] + "…"


def armar(pieza: dict, rec: Recolector, fuente: str, salida: Path, previa: dict | None) -> dict:
    """Convierte las piezas de Facebook al formato que lee la aplicación."""
    previa = previa or {}
    texto = pieza.get("texto") or previa.get("texto") or ""
    imagenes = list(previa.get("imagenes") or [])
    ya = {i.get("id") for i in imagenes}
    for fid in pieza["fotos"]:
        if fid in ya:
            continue
        foto = rec.fotos.get(fid, {})
        img = imagen_local(foto.get("uri"), f"media/{fuente}/f_{fid}.jpg", salida)
        if img:
            imagenes.append({"id": fid, "descripcion_fb": foto.get("descripcion_fb", ""), **img})

    video = previa.get("video")
    vid = pieza.get("video_id")
    if vid:
        v = rec.videos.get(vid, {})
        video = dict(video or {})
        url = v.get("url") or video.get("url") or f"https://www.facebook.com/watch/?v={vid}"
        if url.startswith("/"):
            url = "https://www.facebook.com" + url
        miniatura = video.get("miniatura") or imagen_local(
            v.get("miniatura"), f"media/{fuente}/v_{vid}.jpg", salida
        )
        mp4 = v.get("mp4_temporal") or video.get("mp4_temporal")
        video.update({
            "id": vid,
            "url": url,
            "embed_url": "https://www.facebook.com/plugins/video.php?show_text=false&href="
                         + urllib.request.quote(url, safe=""),
            "miniatura": miniatura,
            "duracion_seg": v.get("duracion_seg") or video.get("duracion_seg"),
            "ancho": v.get("ancho") or video.get("ancho"),
            "alto": v.get("alto") or video.get("alto"),
            "en_vivo": bool(v.get("en_vivo")),
            "mp4_temporal": mp4,
            "mp4_expira": iso(caducidad(mp4)) if caducidad(mp4) else None,
        })

    url = pieza.get("url") or previa.get("url") or (video or {}).get("url") or ""
    if not url and pieza.get("post_id"):
        url = f"https://www.facebook.com/{rec.pagina.get('id_facebook')}/posts/{pieza['post_id']}"
    if video:
        tipo = "reel" if "/reel/" in url and (video.get("alto") or 0) > (video.get("ancho") or 0) else "video"
    else:
        tipo = "album" if len(imagenes) > 1 else "foto" if imagenes else "texto"

    def numero(clave):
        nuevo = pieza.get(clave)
        if clave == "vistas" and vid:
            nuevo = rec.videos.get(vid, {}).get("vistas")
        return nuevo if nuevo is not None else previa.get(clave)

    return {
        "id": pieza["clave"],
        "fuente": fuente,
        "tipo": tipo,
        "titulo": titulo_de(texto),
        "texto": texto,
        "hashtags": list(dict.fromkeys(re.findall(r"#(\w+)", texto))),
        "fecha": iso(pieza["fecha_unix"]),
        "fecha_unix": pieza["fecha_unix"],
        "url": url,
        "reacciones": numero("reacciones"),
        "comentarios": numero("comentarios"),
        "compartidos": numero("compartidos"),
        "vistas": numero("vistas"),
        "imagenes": imagenes,
        "video": video,
        "visto_primera_vez": previa.get("visto_primera_vez") or iso(),
        "actualizado": iso(),
    }


def procesar_pagina(nav: Navegador, cfg: dict, pag: dict, salida: Path,
                    guardadas: dict, abiertas: set, corte: float) -> dict:
    rec = Recolector()
    base = pag["url"].rstrip("/")
    errores = []
    for seccion in ("", "/videos", "/reels", "/photos"):
        try:
            tam = nav.visitar(rec, base + seccion, scrolls=6 if seccion else 4)
            print(f"  {seccion or '/'} leído ({tam // 1024} KB)")
        except Exception as e:
            errores.append(f"{seccion or '/'}: {type(e).__name__}: {e}"[:200])
            print("  ERROR " + errores[-1])

    # Sin sesión la portada muestra muy pocas publicaciones. Abrir cada foto nueva
    # entrega su publicación completa (texto, fecha y enlace).
    conocidas = {i["id"] for p in guardadas.values() for i in p.get("imagenes", [])}
    cubiertas = {f for p in rec.publicaciones() if p.get("texto") for f in p["fotos"]}
    pendientes = [f for f in rec.orden_fotos if f not in conocidas | cubiertas | abiertas]
    limite = cfg.get("max_fotos_a_abrir_por_pagina", 15)
    abiertas_ahora = 0
    antiguas = 0
    for fid in pendientes:
        if abiertas_ahora >= limite or antiguas >= 3:
            break
        if any(fid in p["fotos"] and p.get("texto") for p in rec.publicaciones()):
            continue  # llegó junto con una foto vecina
        try:
            nav.visitar(rec, f"https://www.facebook.com/photo/?fbid={fid}", scrolls=0)
        except Exception as e:
            print(f"  foto {fid}: {type(e).__name__}: {e}"[:200])
            continue
        abiertas_ahora += 1
        suyas = [p for p in rec.publicaciones() if fid in p["fotos"]]
        if suyas:
            abiertas.add(fid)
            antiguas = antiguas + 1 if suyas[0]["fecha_unix"] < corte else 0
        time.sleep(1.5)
    print(f"  fotos abiertas: {abiertas_ahora}")

    nuevas = recibidas = 0
    for pieza in rec.publicaciones():
        if pieza["fecha_unix"] < corte:
            continue
        previa = guardadas.get(pieza["clave"])
        guardadas[pieza["clave"]] = armar(pieza, rec, pag["id"], salida, previa)
        recibidas += 1
        nuevas += previa is None

    info = rec.pagina
    perfil = imagen_local(info.get("foto_perfil"), f"media/{pag['id']}/perfil.jpg", salida, False)
    portada = imagen_local(info.get("portada"), f"media/{pag['id']}/portada.jpg", salida, False)
    ok = recibidas > 0
    print(f"  publicaciones: {recibidas} (nuevas: {nuevas})")
    return {
        "ok": ok,
        "nombre": info.get("nombre") or pag["nombre"],
        "seguidores": info.get("seguidores"),
        "foto_perfil": perfil["url"] if perfil else None,
        "portada": portada["url"] if portada else None,
        "publicaciones_leidas": recibidas,
        "nuevas": nuevas,
        "error": None if ok else (" | ".join(errores) or "Facebook no entregó publicaciones (posible bloqueo)"),
    }


def limpiar_media(salida: Path, publicaciones: list[dict]):
    usados = {"perfil.jpg", "portada.jpg"}
    for p in publicaciones:
        usados.update(Path(i["archivo"]).name for i in p["imagenes"])
        mini = (p.get("video") or {}).get("miniatura")
        if mini:
            usados.add(Path(mini["archivo"]).name)
    for archivo in (salida / "media").glob("*/*.jpg"):
        if archivo.name not in usados:
            archivo.unlink()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--salida", default=str(RAIZ / "datos"))
    ap.add_argument("--config", default=str(RAIZ / "paginas.json"))
    args = ap.parse_args()

    cfg = leer_json(Path(args.config), None)
    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    feed = leer_json(salida / "feed.json", {})
    estado = leer_json(salida / "estado.json", {"fotos_abiertas": []})
    guardadas = {p["id"]: p for p in feed.get("publicaciones", [])}
    fuentes_previas = {f["id"]: f for f in feed.get("fuentes", [])}
    abiertas = set(estado.get("fotos_abiertas", []))
    corte = time.time() - cfg.get("dias_historial", 30) * 86400
    if not TESSERACT:
        print("Aviso: tesseract no está instalado; texto_ocr quedará vacío.")

    fuentes = []
    with sync_playwright() as pw:
        nav = Navegador(pw)
        for pag in cfg["paginas"]:
            print(f"== {pag['nombre']}")
            previa = fuentes_previas.get(pag["id"], {})
            try:
                r = procesar_pagina(nav, cfg, pag, salida, guardadas, abiertas, corte)
            except Exception as e:
                print(f"  ERROR {type(e).__name__}: {e}")
                r = {"ok": False, "nombre": pag["nombre"], "error": f"{type(e).__name__}: {e}"[:300],
                     "publicaciones_leidas": 0, "nuevas": 0}
            # Un fallo pasajero no borra lo que ya se sabía de la página.
            for k in ("seguidores", "foto_perfil", "portada"):
                r[k] = r.get(k) or previa.get(k)
            r["ultima_vez_ok"] = iso() if r["ok"] else previa.get("ultima_vez_ok")
            fuentes.append({"id": pag["id"], "url": pag["url"], **r})
        nav.cerrar()

    publicaciones = sorted(
        (p for p in guardadas.values() if p["fecha_unix"] >= corte),
        key=lambda p: p["fecha_unix"], reverse=True,
    )[: cfg.get("max_publicaciones", 400)]
    limpiar_media(salida, publicaciones)
    guardar_json(salida / "feed.json", {
        "version": 1,
        "actualizado": iso(),
        "total": len(publicaciones),
        "fuentes": fuentes,
        "publicaciones": publicaciones,
    })
    guardar_json(salida / "estado.json", {"fotos_abiertas": sorted(abiertas, key=int)[-1000:]})
    print(f"Listo: {len(publicaciones)} publicaciones en {salida / 'feed.json'}")
    # Si ninguna página respondió, el workflow queda en rojo para que se note.
    return 0 if any(f["ok"] for f in fuentes) else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
