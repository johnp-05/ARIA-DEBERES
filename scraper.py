"""
Scraper para Esemtia — configuración exacta confirmada por inspección.

LOGIN:
  URL:    https://edu.esemtia.ec/LoginEsemtia.aspx
  Campo usuario:   txtBoxUsuario
  Campo password:  txtBoxPassword
  Plataforma:      ASP.NET WebForms → requiere __VIEWSTATE y otros campos ocultos

TAREAS:
  table.tablaTareas
    tr.contenido  id="tarea_XXXX"
      td.materiaClase  → materia
      td.tarea         → título
      td.fecha         → fecha de asignación
      td.fechaEntrega  → fecha de entrega (la que usamos)
    tr id="tareaContent_XXXX"  → detalle oculto con descripción
"""

import logging
import re
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

LOGIN_URL  = "https://edu.esemtia.ec/LoginEsemtia.aspx?microsoft=False&google=False&microsoftEnfant=False&googleEnfant=False"
TAREAS_URL = "https://edu.esemtia.ec/Tareas"   # ajustar si cambia


class EsemtiaScraper:
    def __init__(self, usuario: str, password: str):
        """
        usuario  : tu correo de Esemtia (ej: juan.perez.est@uets.edu.ec)
        password : tu contraseña
        """
        self.usuario  = usuario
        self.password = password
        self.session  = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Origin":  "https://edu.esemtia.ec",
            "Referer": LOGIN_URL,
        })
        self._login()

    # ── Login ─────────────────────────────────────────────────────────────────

    def _login(self):
        """
        Login en ASP.NET WebForms.
        Primero descarga la página para obtener __VIEWSTATE y demás campos
        ocultos, luego hace el POST con todo incluido.
        """
        # 1) GET para obtener los campos ocultos de ASP.NET
        resp = self.session.get(LOGIN_URL, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # 2) Recoger TODOS los inputs ocultos del formulario (ViewState, etc.)
        payload = {}
        form = soup.find("form", {"id": "form1"}) or soup.find("form")
        if form:
            for inp in form.find_all("input", {"type": "hidden"}):
                name  = inp.get("name", "")
                value = inp.get("value", "")
                if name:
                    payload[name] = value
            logger.info(f"Campos ocultos encontrados: {list(payload.keys())}")

        # 3) Agregar credenciales con los nombres exactos confirmados
        payload["txtBoxUsuario"]  = self.usuario
        payload["txtBoxPassword"] = self.password

        # 4) Simular clic en el botón ACCEDER
        #    (puede llamarse btnAcceder, btnLogin, btnIngresar, etc.)
        boton = (
            form.find("input",  {"type": "submit"}) or
            form.find("button", {"type": "submit"}) or
            form.find("input",  {"type": "image"})
        ) if form else None

        if boton and boton.get("name"):
            payload[boton["name"]] = boton.get("value", "Acceder")
            logger.info(f"Botón encontrado: {boton.get('name')}")
        else:
            # Intentar nombres comunes del botón en Esemtia
            for nombre_btn in ["btnAcceder", "btnLogin", "btnIngresar", "Button1"]:
                payload[nombre_btn] = "Acceder"

        # 5) POST al mismo URL (action relativo → mismo URL)
        resp = self.session.post(LOGIN_URL, data=payload, timeout=15, allow_redirects=True)
        resp.raise_for_status()

        # 6) Verificar éxito
        texto = resp.text.lower()
        if "loginError" in resp.text or "contraseña incorrecta" in texto or "usuario incorrecto" in texto:
            raise ValueError("❌ Login fallido: credenciales incorrectas.")
        if "loginEsemtia" in resp.url.lower() or "login" in resp.url.lower():
            raise ValueError("❌ Login fallido: seguimos en la página de login.")

        logger.info(f"✅ Login exitoso. Redirigido a: {resp.url}")

    # ── Obtener tareas ────────────────────────────────────────────────────────

    def obtener_tareas_proximas(self, dias: int = 7) -> list[dict]:
        """Devuelve las tareas cuya fecha de entrega cae en los próximos `dias` días."""
        rutas = [
            "/Tareas",
            "/tareas",
            "/Tareas.aspx",
            "/tareas.aspx",
            "/estudiante/Tareas",
        ]
        for ruta in rutas:
            try:
                url  = f"https://edu.esemtia.ec{ruta}"
                resp = self.session.get(url, timeout=15)
                if resp.status_code == 200 and "tablaTareas" in resp.text:
                    logger.info(f"✅ Tareas encontradas en: {ruta}")
                    return self._parsear_tareas(resp.text, dias)
            except Exception as e:
                logger.debug(f"Ruta {ruta} falló: {e}")

        logger.warning("⚠️ No se encontró la página de tareas.")
        return []

    # ── Parser ────────────────────────────────────────────────────────────────

    def _parsear_tareas(self, html: str, dias: int) -> list[dict]:
        soup   = BeautifulSoup(html, "html.parser")
        hoy    = datetime.now().date()
        limite = hoy + timedelta(days=dias)
        tareas = []
        vistas = set()

        for tabla in soup.select("table.tablaTareas"):
            for fila in tabla.select("tr.contenido"):
                tarea_id = fila.get("id", "")
                if not tarea_id or tarea_id in vistas:
                    continue
                vistas.add(tarea_id)

                materia_td       = fila.select_one("td.materiaClase")
                titulo_td        = fila.select_one("td.tarea")
                fecha_entrega_td = fila.select_one("td.fechaEntrega")

                if not (materia_td and titulo_td and fecha_entrega_td):
                    continue

                fecha_entrega = self._parsear_fecha(fecha_entrega_td.get_text(strip=True))
                if not fecha_entrega or not (hoy <= fecha_entrega <= limite):
                    continue

                descripcion = self._extraer_descripcion(soup, tarea_id)

                tareas.append({
                    "fecha":       fecha_entrega,
                    "materia":     materia_td.get_text(strip=True),
                    "titulo":      titulo_td.get_text(strip=True),
                    "descripcion": descripcion,
                })

        tareas.sort(key=lambda t: t["fecha"])
        logger.info(f"📋 {len(tareas)} tarea(s) en los próximos {dias} días.")
        return tareas

    def _extraer_descripcion(self, soup: BeautifulSoup, tarea_id: str) -> str:
        """Extrae el texto del deber del tr de detalle oculto."""
        contenido_id = tarea_id.replace("tarea_", "tareaContent_")
        contenido_tr = soup.find("tr", {"id": contenido_id})
        if not contenido_tr:
            return ""
        texto = contenido_tr.get_text(" ", strip=True)
        # Extraer solo la parte entre "Tarea:" y "Fecha Entrega"
        match = re.search(
            r"(?:Tarea:|tarea:)\s*(.+?)(?:Fecha Entrega|Fecha:|$)",
            texto, re.IGNORECASE | re.DOTALL,
        )
        return match.group(1).strip()[:250] if match else texto[:200]

    # ── Parsear fecha ─────────────────────────────────────────────────────────

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
