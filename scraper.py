"""
Scraper Esemtia con Playwright Async — compatible con asyncio.
"""

import logging
import re
from datetime import datetime, timedelta

from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

LOGIN_URL = "https://edu.esemtia.ec/LoginEsemtia.aspx?microsoft=False&google=False&microsoftEnfant=False&googleEnfant=False"


class EsemtiaScraper:
    def __init__(self, usuario: str, password: str):
        self.usuario  = usuario
        self.password = password

    async def obtener_tareas_proximas(self, dias: int = 7) -> list[dict]:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()

            try:
                # ── PASO 1: Login ──────────────────────────────────────────
                logger.info("🔐 Abriendo página de login...")
                await page.goto(LOGIN_URL, timeout=20000)
                await page.wait_for_load_state("networkidle", timeout=15000)

                await page.fill("input#txtBoxUsuario", self.usuario)
                await page.fill("input#txtBoxPassword", self.password)

                logger.info("📤 Enviando credenciales con Enter...")
                await page.press("input#txtBoxPassword", "Enter")

                await page.wait_for_load_state("networkidle", timeout=15000)
                logger.info(f"[Login] URL tras clic: {page.url}")

                # ── PASO 2: Página intermedia ──────────────────────────────
                if "edu.esemtia.ec" in page.url:
                    logger.info("🔀 Página intermedia detectada, buscando WEB COMUNICACIÓN...")
                    try:
                        await page.get_by_text(re.compile(r"comunicaci", re.IGNORECASE)).first.click(timeout=8000)
                        await page.wait_for_load_state("networkidle", timeout=15000)
                        logger.info(f"[Selección] URL: {page.url}")
                    except PWTimeout:
                        links = await page.locator("a[href*='comunicacion']").all()
                        if links:
                            await links[0].click()
                            await page.wait_for_load_state("networkidle", timeout=15000)
                        else:
                            await page.goto("https://comunicacion.esemtia.ec/", timeout=15000)
                            await page.wait_for_load_state("networkidle", timeout=15000)

                if "login" in page.url.lower() and "edu.esemtia.ec" in page.url:
                    raise ValueError("❌ Login fallido: usuario o contraseña incorrectos.")

                logger.info(f"✅ Login exitoso → {page.url}")

                # ── PASO 3: Ir a Tareas ────────────────────────────────────
                logger.info("📋 Buscando pestaña Tareas...")
                try:
                    await page.get_by_text(re.compile(r"^tareas$", re.IGNORECASE)).click(timeout=8000)
                    await page.wait_for_load_state("networkidle", timeout=10000)
                    logger.info(f"[Tareas] URL: {page.url}")
                except PWTimeout:
                    logger.warning("No encontré pestaña Tareas, probando URL directa...")
                    await page.goto("https://comunicacion.esemtia.ec/Tareas.aspx", timeout=15000)
                    await page.wait_for_load_state("networkidle", timeout=10000)

                html = await page.content()
                logger.info(f"[Tareas] HTML (600 chars): {html[:600]}")
                return self._parsear_tareas(html, dias)

            except ValueError:
                raise
            except Exception as e:
                logger.error(f"Error en Playwright: {e}")
                raise
            finally:
                await browser.close()

    def _parsear_tareas(self, html: str, dias: int) -> list[dict]:
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

                materia_td       = fila.select_one("td.materiaClase")
                titulo_td        = fila.select_one("td.tarea")
                fecha_entrega_td = fila.select_one("td.fechaEntrega")

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
