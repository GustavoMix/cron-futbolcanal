# cron-futbolcanal

Lee las publicaciones públicas de [FutbolCanal](https://www.facebook.com/FutbolCanal)
y [Entel Gol](https://www.facebook.com/entelgol) y las deja en un JSON para la aplicación Android.

## Cada cuánto actualiza

El workflow se despierta cada 30 minutos y `decidir.py` mira la última lectura:

| Situación | Lee Facebook |
|---|---|
| Partido anunciado (desde 30 min antes hasta 2 h 30 después del inicio) | cada 30 min |
| Había una transmisión en vivo (publicada hace menos de 6 h) | cada 30 min |
| Alguna página falló | cada hora |
| 15 o más publicaciones en las 3 horas previas a la última lectura | cada 2 horas |
| Calma | cada 6 horas |

Las horas de partido salen del texto de las publicaciones ("HOY 14:00",
"Sábado 28 ... 16:00 H"). No se leen de las fotos. GitHub puede retrasar u omitir
turnos. Los tiempos se cambian al inicio de `decidir.py`. Para actualizar al momento:
pestaña Actions, "Run workflow".

Pruebas: `.venv\Scripts\python -m unittest test_decidir`

## Lo que lee la aplicación

```
https://raw.githubusercontent.com/GustavoMix/cron-futbolcanal/datos/feed.json
```

La rama `main` tiene el código; la rama `datos` tiene el JSON y las imágenes
(la crea el workflow en su primera corrida). GitHub puede tardar unos 5 minutos
en servir la versión nueva.

### Formato

```jsonc
{
  "version": 1,
  "actualizado": "2026-11-27T12:35:55-04:00",
  "total": 90,
  "fuentes": [
    { "id": "entelgol", "nombre": "Entel Gol", "url": "...", "seguidores": "196 mil seguidores",
      "foto_perfil": "...", "portada": "...", "ok": true, "error": null,
      "ultima_vez_ok": "...", "publicaciones_leidas": 50, "nuevas": 2 }
  ],
  "publicaciones": [                       // la más nueva primero
    {
      "id": "p_1221425...",                // estable: sirve como clave en la app
      "fuente": "entelgol",
      "tipo": "foto",                      // foto | album | video | reel | texto
      "titulo": "primera línea del texto",
      "texto": "texto completo",
      "hashtags": ["EntelGol"],
      "fecha": "2026-11-27T12:00:01-04:00",
      "fecha_unix": 1795795201,
      "url": "https://www.facebook.com/...",
      "reacciones": 33, "comentarios": null, "compartidos": 0, "vistas": null,
      "imagenes": [
        { "id": "...", "url": "copia en la rama datos", "url_original": "...",
          "ancho": 1080, "alto": 1350,
          "texto_ocr": "texto leído dentro de la imagen",
          "descripcion_fb": "Puede ser una imagen de texto", "archivo": "media/..." }
      ],
      "video": null,                       // o el objeto de abajo
      "visto_primera_vez": "...", "actualizado": "..."
    }
  ]
}
```

Objeto `video`: `id`, `url`, `embed_url`, `miniatura` (igual que una imagen),
`duracion_seg`, `ancho`, `alto`, `en_vivo`, `mp4_temporal`, `mp4_expira`.

- Las **imágenes** se copian al repositorio, así que `imagenes[].url` no caduca
  mientras la publicación siga en el historial.
- Los **videos** no se copian (pesan demasiado). `mp4_temporal` caduca en la fecha
  `mp4_expira` y puede faltar; si no sirve, abre `embed_url` en un WebView o `url` en Facebook.
- `reacciones`, `comentarios`, `compartidos` y `vistas` son `null` cuando Facebook no los entregó.
- `texto_ocr` es lectura automática: sirve para buscar, pero tiene errores en afiches con letras decoradas.

## Kotlin (kotlinx.serialization)

```kotlin
@Serializable data class Feed(
    val actualizado: String, val total: Int,
    val fuentes: List<Fuente>, val publicaciones: List<Publicacion>,
)

@Serializable data class Fuente(
    val id: String, val nombre: String, val url: String, val ok: Boolean,
    val seguidores: String? = null,
    @SerialName("foto_perfil") val fotoPerfil: String? = null,
    val portada: String? = null,
)

@Serializable data class Publicacion(
    val id: String, val fuente: String, val tipo: String,
    val titulo: String, val texto: String, val hashtags: List<String> = emptyList(),
    val fecha: String, @SerialName("fecha_unix") val fechaUnix: Long, val url: String,
    val reacciones: Int? = null, val comentarios: Int? = null,
    val compartidos: Int? = null, val vistas: Int? = null,
    val imagenes: List<Imagen> = emptyList(), val video: Video? = null,
)

@Serializable data class Imagen(
    val url: String, val ancho: Int, val alto: Int,
    @SerialName("texto_ocr") val textoOcr: String = "",
)

@Serializable data class Video(
    val id: String, val url: String,
    @SerialName("embed_url") val embedUrl: String,
    val miniatura: Imagen? = null,
    @SerialName("duracion_seg") val duracionSeg: Int? = null,
    @SerialName("en_vivo") val enVivo: Boolean = false,
    @SerialName("mp4_temporal") val mp4Temporal: String? = null,
    @SerialName("mp4_expira") val mp4Expira: String? = null,
)

val json = Json { ignoreUnknownKeys = true }

suspend fun cargarFeed(): Feed = withContext(Dispatchers.IO) {
    val url = "https://raw.githubusercontent.com/GustavoMix/cron-futbolcanal/datos/feed.json"
    json.decodeFromString<Feed>(URL(url).readText())
}
```

## Partidos de los afiches y escudos

Muchos horarios vienen solo dentro de la imagen. `agenda_ia.py` envía los afiches recientes
a Gemini (plan gratuito de Google), que devuelve equipos, fecha y hora. El resultado sale en
`feed.json`:

- `eventos`: `publicacion`, `fuente`, `inicio_unix`, `fecha`, `hora_confirmada` (falso si el
  afiche solo dice el día), `titulo`, `local`, `visitante`, `competencia`, `canal`. La IA puede
  equivocarse; la publicación original sirve para comprobar.
- `escudos`: `nombres` y `url` de cada equipo que haya en la carpeta `escudos/`.

Para encender la IA, crea una clave en https://aistudio.google.com/apikey (sin activar la
facturación) y guárdala en GitHub como secreto `GEMINI_API_KEY` (Settings, Secrets and
variables, Actions). Sin la clave todo lo demás funciona igual.

Gemini también lee el texto de las publicaciones sin imagen y las miniaturas de los videos, y
**decide cada cuánto leer Facebook** (`ritmo_ia.py`): después de cada lectura recibe el ritmo
real de publicación de cada página (por día y por hora) y los partidos próximos, y responde
cuántos minutos esperar (entre 60 y 720). Queda en `feed.json` como `plan` y `decidir.py` lo
usa; con partido o transmisión en vivo se lee seguido igual, y sin clave rigen los valores
fijos. Usa el mismo secreto `GEMINI_API_KEY`.

Cada evento trae además `categoria` (futbol, automovilismo...), `estadio`, y si el partido se
movió de hora, `hora_cambiada` y `hora_anterior`.

**Escudos:** los que pongas a mano en `escudos/` mandan. Para los demás equipos de la agenda se
buscan solos en TheSportsDB (clave pública gratuita; opcional `THESPORTSDB_KEY`), una sola vez
por equipo, y solo se aceptan resultados de fútbol con un nombre parecido. Quedan en `escudos`
de `feed.json`. Se apagan con `"escudos": {"automaticos": false}` en `paginas.json`.

**Resultados finales:** cuando un afiche da el resultado final, el evento trae `estado`
("finalizado" o "programado"), `marcador_local` y `marcador_visitante`, y se une al partido
anunciado ese día. `decidir.py` deja de leer seguido por un partido ya finalizado. No hay
marcadores en vivo: eso necesita otra fuente de datos.

**Logos de torneos:** `torneos` en `feed.json`, igual que los escudos. Los que pongas a mano
en `escudos/torneos.json` (mismo formato que `equipos.json`) mandan; los demás se buscan solos
en TheSportsDB por el nombre de `competencia`, solo si el nombre se parece mucho.

**Nombres de equipos:** en `eventos` y `tablas` cada equipo sale con un solo nombre ("Betis" y
"Real Betis" son el mismo). Si existe `escudos/equipos.json`, el primer nombre de cada equipo es
el oficial.

**Tablas de posiciones:** `tablas` en `feed.json` (`competencia`, `grupo`, `actualizado`,
`filas` con `posicion`, `equipo`, `puntos`, `jugados`); de cada torneo queda la más nueva. Las
tablas y resultados que ya se habían leído antes de este soporte se releen una sola vez
(hasta 30 días atrás, `ia.dias_releer`).

**Clasificación:** cada publicación trae `ia_clase`: resultado, fixture, transmision, noticia,
promocion u otro. Un solo pedido por corrida para hasta 40 publicaciones (`clasificar_ia.py`).

Cada imagen se analiza una sola vez. En `paginas.json`, `ia` fija el modelo, cuántos días
hacia atrás se miran y cuántas imágenes como máximo por corrida. Si se agota el cupo gratuito,
lo pendiente se lee en otra corrida. En el plan gratuito Google puede usar lo que se le envía.

## Configuración

`paginas.json`: páginas a leer, `dias_historial` (30), `max_publicaciones` (400) y
`max_fotos_a_abrir_por_pagina` (30, subirlo hace la corrida más lenta).
Para agregar otra página basta con añadirla ahí.

## Probar en la PC

```powershell
py -m venv .venv
```
```powershell
.venv\Scripts\pip install -r requirements.txt
```
```powershell
.venv\Scripts\python main.py
```

Usa el Chrome o Edge instalado. El resultado queda en `datos/`. Sin Tesseract
instalado, `texto_ocr` queda vacío (en GitHub Actions se instala solo).

## Límites

- Se lee sin iniciar sesión: solo lo que Facebook muestra a un visitante. No trae
  comentarios (solo contadores, cuando aparecen), historias ni el historial completo.
- Facebook puede bloquear a los servidores de GitHub o cambiar su formato. Cuando pasa,
  la fuente queda con `ok: false`, se conserva lo ya guardado y, si fallan todas, el
  workflow termina en rojo.
- Las condiciones de Facebook restringen la lectura automática y el contenido es de
  sus autores: en la app conviene mostrar la fuente y enlazar la publicación original.
