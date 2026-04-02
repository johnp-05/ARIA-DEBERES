"""
Scraper Esemtia - URLs exactas confirmadas:
  Login:  https://edu.esemtia.ec/LoginEsemtia.aspx
  Tareas: https://comunicacion.esemtia.ec/Ejercicios.aspx
"""

import logging
import re
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

LOGIN_URL  = "https://edu.esemtia.ec/LoginEsemtia.aspx?microsoft=False&google=False&microsoftEnfant=False&googleEnfant=False"
TAREAS_URL = "https://comunicacion.esemtia.ec/Ejercicios.aspx"


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
        })
        self._login()

    def _login(self):
        # 1) GET para obtener ViewState y campos ocultos de ASP.NET
        resp = self.session.get(LOGIN_URL, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # 2) Recoger TODOS los inputs ocultos
        payload = {}
        form = soup.find("form", {"id": "form1"}) or soup.find("form")
        if form:
            for inp in form.find_all("input", {"type": "hidden"}):
                name = inp.get("name", "")
                if name:
                    payload[name] = inp.get("value", "")

            # Incluir el botón de submit (necesario para ASP.NET)
            btn = form.find("input", {"type": "submit"}) or form.find("button", {"type": "submit"})
            if btn and btn.get("name"):
                payload[btn.get("name")] = btn.get("value", "")

        # 3) Credenciales con nombres exactos confirmados
        payload["txtBoxUsuario"]  = self.usuario
        payload["txtBoxPassword"] = self.password

        # DEBUG: ver qué campos se están enviando
        logger.info(f"Campos del payload (sin contraseña): { {k: v for k, v in payload.items() if 'pass' not in k.lower() and 'password' not in k.lower()} }")

        # 4) POST login
        resp = self.session.post(
            LOGIN_URL, data=payload, timeout=15, allow_redirects=True
        )
        resp.raise_for_status()

        # DEBUG: ver a dónde redirigió y qué devolvió
        logger.info(f"URL final tras login: {resp.url}")
        logger.info(f"Status code: {resp.status_code}")
        logger.info(f"HTML inicio (primeros 800 chars): {resp.text[:800]}")

        # 5) Verificar éxito — solo falla si SIGUE en edu.esemtia.ec con login en la URL
        if "edu.esemtia.ec" in resp.url and "login" in resp.url.lower():
            raise ValueError("❌ Login fallido: usuario o contraseña incorrectos.")

        logger.info(f"✅ Login exitoso → {resp.url}")

    def obtener_tareas_proximas(self, dias: int = 7) -> list[dict]:
        resp = self.session.get(TAREAS_URL, timeout=15)
        resp.raise_for_status()

        if "tablaTareas" not in resp.text:
            logger.warning("⚠️ La página de tareas no tiene el formato esperado.")
            logger.info(f"HTML tareas (primeros 800 chars): {resp.text[:800]}")
            return []

        return self._parsear_tareas(resp.text, dias)

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
        contenido_id = tarea_id.replace("tarea_", "tareaContent_")
        contenido_tr = soup.find("tr", {"id": contenido_id})
        if not contenido_tr:
            return ""
        texto = contenido_tr.get_text(" ", strip=True)
        match = re.search(
            r"(?:Tarea:|tarea:)\s*(.+?)(?:Fecha Entrega|Fecha:|$)",
            texto, re.IGNORECASE | re.DOTALL,
        )
        return match.group(1).strip()[:250] if match else texto[:200]

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
