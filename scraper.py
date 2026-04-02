"""
Scraper Esemtia — flujo completo:
  1. Login en edu.esemtia.ec/LoginEsemtia.aspx
  2. Página intermedia → elegir "WEB COMUNICACIÓN"
  3. Ir a Tareas en comunicacion.esemtia.ec
"""

import logging
import re
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

LOGIN_URL = "https://edu.esemtia.ec/LoginEsemtia.aspx?microsoft=False&google=False&microsoftEnfant=False&googleEnfant=False"
BASE_COM  = "https://comunicacion.esemtia.ec"


class EsemtiaScraper:
    def __init__(self, usuario: str, password: str):
        self.usuario  = usuario
        self.password = password
        self.session  = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-EC,es;q=0.9,en;q=0.8",
        })
        self._login()

    # ──────────────────────────────────────────────────────────────────────────
    # PASO 1 — Login
    # ──────────────────────────────────────────────────────────────────────────
    def _login(self):
        resp = self.session.get(LOGIN_URL, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Recoger todos los campos ocultos del form ASP.NET
        payload = {}
        form = soup.find("form", {"id": "form1"}) or soup.find("form")
        if form:
            for inp in form.find_all("input"):
                name = inp.get("name", "")
                tipo = inp.get("type", "text").lower()
                if name and tipo != "password":
                    payload[name] = inp.get("value", "")

        logger.info(f"Campos del form: {list(payload.keys())}")

        # Credenciales
        payload["txtBoxUsuario"]  = self.usuario
        payload["txtBoxPassword"] = self.password

        # POST login
        resp = self.session.post(
            LOGIN_URL,
            data=payload,
            headers={
                "Referer": LOGIN_URL,
                "Origin": "https://edu.esemtia.ec",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=15,
            allow_redirects=True,
        )
        resp.raise_for_status()

        logger.info(f"[Login] URL tras POST: {resp.url}")
        logger.info(f"[Login] HTML (600 chars): {resp.text[:600]}")

        # ── PASO 2 — Página intermedia de selección ───────────────────────────
        # Si todavía estamos en edu.esemtia.ec y hay botones de selección,
        # buscamos el enlace a "WEB COMUNICACIÓN" y lo seguimos
        if "edu.esemtia.ec" in resp.url:
            resp = self._elegir_web_comunicacion(resp)

        # Verificar que llegamos a comunicacion.esemtia.ec
        if "edu.esemtia.ec" in resp.url and "login" in resp.url.lower():
            raise ValueError("❌ Login fallido: usuario o contraseña incorrectos.")

        logger.info(f"✅ Sesión iniciada → {resp.url}")

    # ──────────────────────────────────────────────────────────────────────────
    # PASO 2 — Elegir "WEB COMUNICACIÓN" en la página intermedia
    # ──────────────────────────────────────────────────────────────────────────
    def _elegir_web_comunicacion(self, resp: requests.Response) -> requests.Response:
        soup = BeautifulSoup(resp.text, "html.parser")

        # Buscar enlace o botón que apunte a comunicacion.esemtia.ec
        # o que contenga el texto "comunicación" / "WEB"
        url_destino = None

        for tag in soup.find_all(["a", "input", "button"]):
            href  = tag.get("href", "") or tag.get("onclick", "") or tag.get("value", "")
            texto = tag.get_text(strip=True).lower()
            if "comunicaci" in texto or "comunicacion" in href.lower() or "comunicacion.esemtia" in href.lower():
                if href.startswith("http"):
                    url_destino = href
                elif href.startswith("/"):
                    url_destino = "https://edu.esemtia.ec" + href
                logger.info(f"[Selección] Enlace WEB COMUNICACIÓN encontrado: {url_destino}")
                break

        # Si no encontramos el enlace por texto, ir directamente
        if not url_destino:
            logger.warning("[Selección] No encontré enlace a WEB COMUNICACIÓN, probando URL directa")
            url_destino = BASE_COM + "/"

        resp2 = self.session.get(url_destino, timeout=15, allow_redirects=True)
        resp2.raise_for_status()
        logger.info(f"[Selección] URL tras elegir WEB COMUNICACIÓN: {resp2.url}")
        return resp2

    # ──────────────────────────────────────────────────────────────────────────
    # PASO 3 — Obtener tareas
    # ──────────────────────────────────────────────────────────────────────────
    def obtener_tareas_proximas(self, dias: int = 7) -> list[dict]:
        # La pestaña "Tareas" en comunicacion.esemtia.ec
        # Probamos las URLs más comunes
        urls_candidatas = [
            BASE_COM + "/Tareas.aspx",
            BASE_COM + "/Ejercicios.aspx",
            BASE_COM + "/Alumno/Tareas.aspx",
            BASE_COM + "/Comunicacion/Tareas.aspx",
        ]

        html = None
        for url in urls_candidatas:
            try:
                resp = self.session.get(url, timeout=15, allow_redirects=True)
                if resp.status_code == 200 and len(resp.text) > 500:
                    logger.info(f"✅ Página de tareas encontrada: {resp.url}")
                    html = resp.text
                    break
            except Exception as e:
                logger.warning(f"URL {url} falló: {e}")

        if not html:
            logger.warning("⚠️ No se encontró la página de tareas.")
            return []

        logger.info(f"HTML tareas (600 chars): {html[:600]}")
        return self._parsear_tareas(html, dias)

    # ──────────────────────────────────────────────────────────────────────────
    # Parser — compatible con la tabla vista en pantalla
    # Columnas: Materia/Clase | Tarea | Fecha | Fecha Entrega
    # ──────────────────────────────────────────────────────────────────────────
    def _parsear_tareas(self, html: str, dias: int) -> list[dict]:
        soup   = BeautifulSoup(html, "html.parser")
        hoy    = datetime.now().date()
        limite = hoy + timedelta(days=dias)
        tareas = []
        vistas = set()

        # Buscar tablas que contengan "Fecha Entrega" en el encabezado
        for tabla in soup.find_all("table"):
            encabezados = [th.get_text(strip=True).lower() for th in tabla.find_all("th")]
            tiene_fecha_entrega = any("entrega" in e for e in encabezados)
            tiene_materia       = any("materia" in e for e in encabezados)

            if not (tiene_fecha_entrega or tiene_materia):
                # También probar con clases CSS del scraper original
                pass

            for fila in tabla.find_all("tr"):
                celdas = fila.find_all("td")
                if len(celdas) < 3:
                    continue

                # Intentar con clases CSS (formato original)
                materia_td       = fila.select_one("td.materiaClase")
                titulo_td        = fila.select_one("td.tarea")
                fecha_entrega_td = fila.select_one("td.fechaEntrega")

                # Si no hay clases, usar posición por columna según lo visto en pantalla
                # Columnas: Materia/Clase(0) | Tarea(1) | Fecha(2) | Fecha Entrega(3)
                if not (materia_td and titulo_td and fecha_entrega_td):
                    if len(celdas) >= 4:
                        materia_td       = celdas[0]
                        titulo_td        = celdas[1]
                        fecha_entrega_td = celdas[3]  # Fecha Entrega es la 4ª columna
                    elif len(celdas) == 3:
                        materia_td       = celdas[0]
                        titulo_td        = celdas[1]
                        fecha_entrega_td = celdas[2]
                    else:
                        continue

                materia = materia_td.get_text(strip=True)
                titulo  = titulo_td.get_text(strip=True)
                fecha_texto = fecha_entrega_td.get_text(strip=True)

                # Ignorar filas vacías o de encabezado
                if not materia or not titulo or not fecha_texto:
                    continue
                if materia.lower() in ("materia", "materia/clase", "clase"):
                    continue

                clave = f"{materia}|{titulo}"
                if clave in vistas:
                    continue
                vistas.add(clave)

                fecha_entrega = self._parsear_fecha(fecha_texto)
                if not fecha_entrega or not (hoy <= fecha_entrega <= limite):
                    continue

                tareas.append({
                    "fecha":       fecha_entrega,
                    "materia":     materia,
                    "titulo":      titulo,
                    "descripcion": "",
                })

        tareas.sort(key=lambda t: t["fecha"])
        logger.info(f"📋 {len(tareas)} tarea(s) en los próximos {dias} días.")
        return tareas

    def _parsear_fecha(self, texto: str):
        texto = texto.strip()
        for fmt in ["%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%y", "%d/%m/%y"]:
            try:
                return datetime.strptime(texto, fmt).date()
            except ValueError:
                continue
        match = re.search(r"\d{2}[-/]\d{2}[-/]\d{2,4}", texto)
        if match:
            return self._parsear_fecha(match.group())
        return None
