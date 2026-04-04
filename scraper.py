"""
Scraper Esemtia con Playwright Async — obtiene titulo completo entrando a cada tarea.
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
                await page.goto(LOGIN_URL, timeout=20000)
                await page.wait_for_load_state("networkidle", timeout=15000)
                await page.fill("input#txtBoxUsuario", self.usuario)
                await page.fill("input#txtBoxPassword", self.password)
                await page.press("input#txtBoxPassword", "Enter")
                await page.wait_for_load_state("networkidle", timeout=15000)
                logger.info(f"[Login] URL: {page.url}")

                # ── PASO 2: WEB COMUNICACION ───────────────────────────────
                if "edu.esemtia.ec" in page.url:
                    try:
                        await page.wait_for_selector("text=WEB COMUNICACIÓN", timeout=8000)
                        await page.get_by_text("WEB COMUNICACIÓN").click()
                        await page.wait_for_url("*comunicacion.esemtia.ec*", timeout=15000)
                    except PWTimeout:
                        await page.goto("https://comunicacion.esemtia.ec/", timeout=15000)
                        await page.wait_for_load_state("networkidle", timeout=15000)

                if "login" in page.url.lower() and "edu.esemtia.ec" in page.url:
                    raise ValueError("Login fallido: usuario o contrasena incorrectos.")

                logger.info(f"En portal: {page.url}")

                # ── PASO 3: Ir a Tareas ────────────────────────────────────
                try:
                    await page.wait_for_selector("text=Tareas", timeout=8000)
                    await page.get_by_text("Tareas").first.click()
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except PWTimeout:
                    for path in ["Ejercicios.aspx", "Alumno/Tareas.aspx", "Default.aspx"]:
                        try:
                            r = await page.goto(f"https://comunicacion.esemtia.ec/{path}", timeout=10000)
                            if r and r.status == 200:
                                break
                        except Exception:
                            continue

                tareas_url = page.url
                logger.info(f"[Tareas] URL: {tareas_url}")

                # ── PASO 4: Parsear tabla y entrar a cada tarea ────────────
                return await self._extraer_tareas(page, tareas_url, dias)

            except ValueError:
                raise
            except Exception as e:
                logger.error(f"Error en Playwright: {e}")
                raise
            finally:
                await browser.close()

    async def _extraer_tareas(self, page, tareas_url: str, dias: int) -> list[dict]:
        hoy    = datetime.now().date()
        limite = hoy + timedelta(days=dias)
        tareas = []
        vistas = set()

        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        # Recopilar info básica de la tabla primero
        candidatas = []
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

                # Buscar link dentro de la celda del título
                link = titulo_td.find("a")
                href = link.get("href", "") if link else ""

                candidatas.append({
                    "fecha":   fecha_entrega,
                    "materia": materia,
                    "titulo":  titulo,
                    "href":    href,
                })

        # Ahora entrar a cada tarea para obtener título completo
        for c in candidatas:
            titulo_completo = c["titulo"]
            descripcion = ""

            if c["href"]:
                try:
                    url_detalle = c["href"] if c["href"].startswith("http") else f"https://comunicacion.esemtia.ec/{c['href']}"
                    await page.goto(url_detalle, timeout=10000)
                    await page.wait_for_load_state("networkidle", timeout=8000)

                    detalle_html = await page.content()
                    detalle_soup = BeautifulSoup(detalle_html, "html.parser")

                    # Buscar título completo
                    for sel in ["h1", "h2", ".titulo", ".tituloTarea", "#lblTitulo", ".tarea"]:
                        elem = detalle_soup.select_one(sel)
                        if elem:
                            texto = elem.get_text(strip=True)
                            if texto and len(texto) > 3:
                                titulo_completo = texto
                                break

                    # Buscar descripción
                    for sel in [".descripcion", ".detalle", "#lblDescripcion", "p"]:
                        elem = detalle_soup.select_one(sel)
                        if elem:
                            texto = elem.get_text(strip=True)
                            if texto and len(texto) > 5:
                                descripcion = texto[:300]
                                break

                    logger.info(f"Detalle: {titulo_completo[:60]}")
                    await page.goto(tareas_url, timeout=10000)
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except Exception as ex:
                    logger.warning(f"No pude entrar al detalle: {ex}")

            tareas.append({
                "fecha":       c["fecha"],
                "materia":     c["materia"],
                "titulo":      titulo_completo,
                "descripcion": descripcion,
            })

        tareas.sort(key=lambda t: t["fecha"])
        logger.info(f"{len(tareas)} tarea(s) encontradas.")
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
