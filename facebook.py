"""Lectura de una página pública de Facebook sin iniciar sesión.

Facebook manda los datos de cada publicación como JSON dentro del HTML (y en las
respuestas GraphQL). Leer ese JSON es mucho más estable que leer lo que se ve en
pantalla: trae el texto completo, la fecha exacta, las fotos y los videos.

No inicia sesión, no resuelve captchas y no elude controles de acceso: solo lee
lo que Facebook le muestra a un visitante sin cuenta.
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlsplit

SCRIPT_JSON = re.compile(r'<script type="application/json"[^>]*>(.*?)</script>', re.S)


def recorrer(o):
    """Todos los diccionarios de un JSON, a cualquier profundidad."""
    pila = [o]
    while pila:
        x = pila.pop()
        if isinstance(x, dict):
            yield x
            pila.extend(reversed(list(x.values())))
        elif isinstance(x, list):
            pila.extend(reversed(x))


def dic(o) -> dict:
    return o if isinstance(o, dict) else {}


def entero(o):
    if isinstance(o, bool):
        return None
    if isinstance(o, int):
        return o
    return int(o) if isinstance(o, str) and o.isdigit() else None


def es_historia(i) -> bool:
    # Los id de publicación son base64 de "S:_I<pagina>:..."
    return isinstance(i, str) and i.startswith("Uzpf")


class Recolector:
    """Va juntando piezas sueltas (historias, fotos, videos) de varias vistas."""

    def __init__(self):
        self.pagina: dict = {}
        self.historias: dict[str, dict] = {}
        self.fotos: dict[str, dict] = {}
        self.videos: dict[str, dict] = {}
        self.orden_fotos: list[str] = []  # como salen en la pestaña Fotos: más nueva primero

    # ---------- entrada ----------
    def leer(self, html: str, respuestas: list[str]):
        bloques = []
        for m in SCRIPT_JSON.finditer(html):
            try:
                bloques.append(json.loads(m.group(1)))
            except ValueError:
                pass
        for cuerpo in respuestas:
            for linea in cuerpo.splitlines():
                try:
                    bloques.append(json.loads(linea))
                except ValueError:
                    pass
        for bloque in bloques:
            for d in recorrer(bloque):
                self._cabecera(d)
                self._historia(d)
                self._foto(d)
                self._video(d)

    # ---------- piezas ----------
    def _cabecera(self, d):
        user = dic(dic(d.get("profile_header_renderer")).get("user"))
        if not user:
            return
        p = self.pagina
        p.setdefault("id_facebook", d.get("id") or user.get("id"))
        if user.get("name"):
            p.setdefault("nombre", user["name"])
        for x in recorrer(user):
            foto = dic(x.get("profilePicLarge")).get("uri")
            if foto:
                p.setdefault("foto_perfil", foto)
            portada = dic(dic(dic(x.get("cover_photo")).get("photo")).get("image")).get("uri")
            if portada:
                p.setdefault("portada", portada)
            t = x.get("text")
            if isinstance(t, str) and re.search(r"seguidores|followers", t) and len(t) < 40:
                p.setdefault("seguidores", t.replace("\xa0", " "))

    def _h(self, sid: str) -> dict:
        return self.historias.setdefault(
            sid, {"fotos": [], "videos": [], "autores": set()}
        )

    def _historia(self, d):
        sid = d.get("id")
        texto = dic(d.get("message")).get("text")

        # En la vista de una foto el texto viene al lado de "container_story".
        if texto:
            destino = sid if es_historia(sid) else dic(d.get("container_story")).get("id")
            if es_historia(destino):
                self._h(destino)["texto"] = texto

        # Foto -> publicación que la contiene
        if d.get("__typename") == "Photo" or "currMedia" in d:
            foto = dic(d.get("currMedia")) or d
            cont = dic(foto.get("container_story")) or dic(foto.get("creation_story"))
            if es_historia(cont.get("id")) and foto.get("id"):
                h = self._h(cont["id"])
                self._agregar(h["fotos"], foto["id"])

        if not es_historia(sid):
            return
        h = self._h(sid)
        if d.get("post_id"):
            h["post_id"] = str(d["post_id"])
        if entero(d.get("creation_time")):
            h["fecha_unix"] = entero(d["creation_time"])
        for clave in ("wwwURL", "url"):
            u = d.get(clave)
            if isinstance(u, str) and re.search(r"facebook\.com/.+/(posts|videos)/|/reel/", u):
                h.setdefault("url", u)
        for a in d.get("actors") or []:
            if dic(a).get("id"):
                h["autores"].add(str(a["id"]))
        for adj in d.get("attachments") or []:
            self._adjunto(h, dic(adj))
        # Contadores: solo en el nodo completo de la publicación.
        if d.get("post_id") and ("comet_sections" in d or "feedback" in d):
            for k, v in self.contadores(d).items():
                h.setdefault(k, v)

    def _adjunto(self, h, adj):
        for x in recorrer(adj):
            tipo, mid = x.get("__typename"), x.get("id")
            if not mid:
                continue
            if tipo == "Photo":
                self._agregar(h["fotos"], mid)
            elif tipo == "Video":
                self._agregar(h["videos"], mid)

    @staticmethod
    def _agregar(lista, valor):
        if valor not in lista:
            lista.append(valor)

    @staticmethod
    def contadores(nodo) -> dict:
        r: dict = {}
        for x in recorrer(nodo):
            for clave, destino, sub in (
                ("reaction_count", "reacciones", "count"),
                ("share_count", "compartidos", "count"),
                ("comment_rendering_instance", "comentarios", "total_count"),
                ("comments", "comentarios", "total_count"),
            ):
                v = entero(dic(x.get(clave)).get(sub))
                if v is not None:
                    r.setdefault(destino, v)
        return r

    def _foto(self, d):
        nodo = dic(d.get("currMedia")) or d
        if nodo.get("__typename") != "Photo" or not nodo.get("id"):
            return
        f = self.fotos.setdefault(nodo["id"], {})
        for clave in ("viewer_image", "photo_image", "image"):
            img = dic(nodo.get(clave))
            uri = img.get("uri")
            if not uri or "rsrc.php" in uri:
                continue
            ancho = entero(img.get("width")) or entero(dic(nodo.get("viewer_image")).get("width")) or 0
            # viewer_image suele ser la versión grande; se prefiere sobre las demás.
            peso = {"viewer_image": 3, "image": 2, "photo_image": 1}[clave]
            if peso > f.get("_peso", 0):
                f.update(uri=uri, ancho=ancho or None, _peso=peso)
        cap = nodo.get("accessibility_caption")
        if cap:
            f["descripcion_fb"] = cap
        if "viewer_image" in nodo and "accessibility_caption" in nodo:
            self._agregar(self.orden_fotos, nodo["id"])

    def _video(self, d):
        legado = dic(d.get("videoDeliveryLegacyFields"))
        vid = d.get("id") if d.get("__typename") == "Video" else None
        marcas = ("playable_duration_in_ms", "publish_time", "preferred_thumbnail", "permalink_url")
        if not vid and (legado or any(k in d for k in marcas)):
            vid = d.get("id") or legado.get("id")
        if not (isinstance(vid, str) and vid.isdigit()):
            return
        v = self.videos.setdefault(vid, {})

        def poner(clave, valor):
            if valor not in (None, "", 0):
                v.setdefault(clave, valor)

        ms = entero(d.get("playable_duration_in_ms"))
        poner("duracion_seg", round(ms / 1000) if ms else None)
        poner("fecha_unix", entero(d.get("publish_time")))
        poner("miniatura", dic(dic(d.get("preferred_thumbnail")).get("image")).get("uri")
              or dic(d.get("thumbnailImage")).get("uri") or dic(d.get("image")).get("uri"))
        poner("url", d.get("permalink_url") or d.get("shareable_url") or d.get("canonical_uri_with_fallback"))
        poner("vistas", entero(d.get("play_count")))
        poner("ancho", entero(d.get("width")) or entero(d.get("original_width")))
        poner("alto", entero(d.get("height")) or entero(d.get("original_height")))
        poner("mp4_temporal", legado.get("browser_native_hd_url") or legado.get("browser_native_sd_url"))
        poner("titulo", dic(d.get("savable_title")).get("text"))
        poner("dueno", dic(d.get("owner")).get("id"))
        if d.get("is_live_streaming") is True or d.get("broadcast_status") == "LIVE":
            v["en_vivo"] = True
        for k, val in self.contadores(dic(d.get("feedback"))).items():
            poner_contador = v.setdefault("contadores", {})
            poner_contador.setdefault(k, val)
        historia = dic(d.get("creation_story"))
        if es_historia(historia.get("id")):
            h = self._h(historia["id"])
            self._agregar(h["videos"], vid)
            if dic(historia.get("message")).get("text"):
                h.setdefault("texto", historia["message"]["text"])

    # ---------- salida ----------
    def publicaciones(self) -> list[dict]:
        """Une las piezas en publicaciones de ESTA página."""
        propio = str(self.pagina.get("id_facebook") or "")
        salida: dict[str, dict] = {}

        def unir(clave, datos):
            p = salida.setdefault(clave, {"fotos": [], "video_id": None})
            for k, val in datos.items():
                if k == "fotos":
                    for f in val:
                        self._agregar(p["fotos"], f)
                elif val not in (None, ""):
                    if not p.get(k):
                        p[k] = val

        for sid, h in self.historias.items():
            if propio and h["autores"] and propio not in h["autores"]:
                continue
            vid = next((v for v in h["videos"] if v in self.videos), None)
            if vid:
                v = self.videos[vid]
                if propio and not h["autores"] and v.get("dueno") not in (None, propio):
                    continue
                clave = "v_" + vid
            elif h.get("post_id"):
                clave = "p_" + h["post_id"]
            else:
                continue
            unir(clave, {
                "post_id": h.get("post_id"), "texto": h.get("texto"),
                "fecha_unix": h.get("fecha_unix") or (self.videos[vid].get("fecha_unix") if vid else None),
                "url": h.get("url"), "video_id": vid,
                "fotos": [] if vid else h["fotos"],
                "reacciones": h.get("reacciones"), "comentarios": h.get("comentarios"),
                "compartidos": h.get("compartidos"),
            })
            if vid:
                unir(clave, self.videos[vid].get("contadores", {}))

        # Videos que no vinieron acompañados de su publicación
        for vid, v in self.videos.items():
            if "v_" + vid not in salida and v.get("fecha_unix") and (not propio or v.get("dueno") == propio):
                unir("v_" + vid, {"video_id": vid, "texto": v.get("titulo"),
                                  "fecha_unix": v["fecha_unix"], "url": v.get("url")})
                unir("v_" + vid, v.get("contadores", {}))

        # Las sub-historias de un álbum llegan vacías (sin texto, fotos ni video): se descartan.
        return [
            {"clave": k, **p} for k, p in salida.items()
            if p.get("fecha_unix") and (p.get("texto") or p["fotos"] or p["video_id"])
        ]


def caducidad(url: str | None) -> int | None:
    """Las URL del CDN de Facebook traen su vencimiento en 'oe' (unix en hexadecimal)."""
    if not url:
        return None
    try:
        return int(parse_qs(urlsplit(url).query)["oe"][0], 16)
    except (KeyError, ValueError):
        return None


class Navegador:
    def __init__(self, playwright):
        errores = []
        for canal in ("chrome", "msedge", None):
            try:
                self.browser = playwright.chromium.launch(channel=canal, headless=True)
                break
            except Exception as e:  # se prueba el siguiente navegador
                errores.append(f"{canal}: {e}")
        else:
            raise RuntimeError("No se pudo abrir ningún navegador: " + " | ".join(errores))

    def visitar(self, rec: Recolector, url: str, scrolls: int = 5, espera: int = 3500) -> int:
        """Abre una URL, la recorre y entrega los datos al recolector. Devuelve bytes leídos."""
        ctx = self.browser.new_context(locale="es-BO", viewport={"width": 1280, "height": 1800})
        try:
            page = ctx.new_page()
            # Solo interesan los datos: no bajar imágenes, videos ni fuentes ahorra mucho tiempo.
            page.route("**/*", lambda ruta: ruta.abort()
                       if ruta.request.resource_type in ("image", "media", "font")
                       else ruta.continue_())
            respuestas = []
            page.on("response", lambda r: respuestas.append(r) if "/api/graphql" in r.url else None)
            page.goto(url, wait_until="commit", timeout=45000)
            page.wait_for_timeout(espera)
            # El aviso de "inicia sesión" tapa la página y frena el scroll.
            for nombre in ("Rechazar cookies opcionales", "Decline optional cookies", "Cerrar", "Close"):
                try:
                    page.get_by_role("button", name=nombre).first.click(timeout=600)
                except Exception:
                    pass
            for _ in range(scrolls):
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(800)
            cuerpos = []
            for r in respuestas:
                try:
                    cuerpos.append(r.text())
                except Exception:
                    pass
            try:
                html = page.content()
            except Exception:  # a veces la página justo está recargando
                page.wait_for_timeout(2000)
                html = page.content()
            rec.leer(html, cuerpos)
            return len(html)
        finally:
            ctx.close()

    def cerrar(self):
        self.browser.close()
