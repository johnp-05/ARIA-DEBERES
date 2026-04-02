"""
Scraper Esemtia con Playwright — maneja el login JavaScript correctamente.
Flujo:
  1. Login → ACCEDER (JavaScript)
  2. Página intermedia → WEB COMUNICACIÓN
  3. Pestaña Tareas → extraer tabla
"""

import logging
import re
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

logger = logging.getLogger(__name__)

LOGIN_URL = "https://edu.esemtia.ec/LoginEsemtia.aspx?microsoft=False&google=False&microsoftEnfant=False&googleEnfant=False"


class EsemtiaScraper:
    def __init__(self, usuario: str, password: str):
        self.usuario  = usuario
        self.password = password

    def obtener_tareas_proximas(self, dias: int = 7) -> list[dict]:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
            page = context.new_page()

            try:
                # ── PASO 1: Login ──────────────────────────────────────────
                logger.info("🔐 Abriendo página de login...")
                page.goto(LOGIN_URL, timeout=20000)
                page.wait_for_load_state("networkidle", timeout=15000)

                # Llenar usuario y contraseña
                page.fill("input#txtBoxUsuario", self.usuario)
                page.fill("input#txtBoxPassword", self.password)

                logger.info("📤 Enviando credenciales...")
                # Clic en el botón ACCEDER (puede tener distintos selectores)
                try:
                    page.click("input[type='submit']", timeout=5000)
                except PWTimeout:
                    try:
                        page.click("button[type='submit']", timeout=5000)
                    except PWTimeout:
                        # Buscar por texto "ACCEDER"
                        page.get_by_text("ACCEDER").click(timeout=5000)

                page.wait_for_load_state("networkidle", timeout=15000)
                logger.info(f"[Login] URL tras clic: {page.url}")

                # ── PASO 2: Página intermedia ──────────────────────────────
                # Si hay selección de portal, elegir WEB COMUNICACIÓN
                if "edu.esemtia.ec" in page.url:
                    logger.info("🔀 Detectada página intermedia, buscando WEB COMUNICACIÓN...")
                    try:
                        # Buscar por texto que contenga "comunicaci"
                        page.get_by_text(re.compile(r"comunicaci", re.IGNORECASE)).first.click(timeout=8000)
                        page.wait_for_load_state("networkidle", timeout=15000)
                        logger.info(f"[Selección] URL tras elegir portal: {page.url}")
                    except PWTimeout:
                        logger.warning("No encontré botón WEB COMUNICACIÓN, probando links...")
                        # Buscar links que apunten a comunicacion.esemtia.ec
                        links = page.locator("a[href*='comunicacion']").all()
                        if links:
                            links[0].click()
                            page.wait_for_load_state("networkidle", timeout=15000)
                        else:
                            page.goto("https://comunicacion.esemtia.ec/", timeout=15000)
                            page.wait_for_load_state("networkidle", timeout=15000)

                # Verificar login exitoso
                if "login" in page.url.lower() and "edu.esemtia.ec" in page.url:
                    raise ValueError("❌ Login fallido: usuario o contraseña incorrectos.")

                logger.info(f"✅ Login exitoso → {page.url}")

                # ── PASO 3: Ir a Tareas ────────────────────────────────────
                logger.info("📋 Buscando pestaña Tareas...")
                try:
                    page.get_by_text(re.compile(r"tareas", re.IGNORECASE)).first.click(timeout=8000)
                    page.wait_for_load_state("networkidle", timeout=10000)
                    logger.info(f"[Tareas] URL: {page.url}")
                except PWTimeout:
                    logger.warning("No encontré pestaña Tareas, probando URL directa...")
                    page.goto("https://comunicacion.esemtia.ec/Tareas.aspx", timeout=15000)
                    page.wait_for_load_state("networkidle", timeout=10000)

                html = page.content()
                logger.info(f"[Tareas] HTML (600 chars): {html[:600]}")
                return self._parsear_tareas(html, dias)

            except ValueError:
                raise
            except Exception as e:
                logger.error(f"Error en Playwright: {e}")
                raise
            finally:
                browser.close()

    # ──────────────────────────────────────────────────────────────────────────
    def _parsear_tareas(self, html: str, dias: int) -> list[dict]:
        from bs4 import BeautifulSoup
        soup   = BeautifulSoup(html, "html.parser")
        hoy    = datetime.now().date()
        limite = hoy + timedelta(days=dias)
        tareas = []
        vistas = set()

        for tabla in soup.find_all("table"):
            for fila in tabla.find_all("tr"):
                celdas = fila.find_all("td")
                if len(celdas) < 3:
                    continue

                # Formato con clases CSS
                materia_td       = fila.select_one("td.materiaClase")
                titulo_td        = fila.select_one("td.tarea")
                fecha_entrega_td = fila.select_one("td.fechaEntrega")

                # Formato por posición (Materia | Tarea | Fecha | Fecha Entrega)
                if not (materia_td and titulo_td and fecha_entrega_td):
                    if len(celdas) >= 4:
                        materia_td, titulo_td = celdas[0], celdas[1]
                        fecha_entrega_td = celdas[3]
                    elif len(celdas) == 3:
                        materia_td, titulo_td, fecha_entrega_td = celdas[0], celdas[1], celdas[2]
                    else:
                        continue

                materia     = materia_td.get_text(strip=True)
                titulo      = titulo_td.get_text(strip=True)
                fecha_texto = fecha_entrega_td.get_text(strip=True)

                if not materia or not titulo or not fecha_texto:
                    continue
                if materia.lower() in ("materia", "materia/clase", "clase", "tarea", "fecha"):
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
