"""
Scraper Esemtia con Playwright Async + resumen con Gemini.
"""

import logging
import os
import re
import json
from datetime import datetime, timedelta

import google.generativeai as genai
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

LOGIN_URL = "https://edu.esemtia.ec/LoginEsemtia.aspx?microsoft=False&google=False&microsoftEnfant=False&googleEnfant=False"

# Configurar Gemini
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini = genai.GenerativeModel("gemini-2.0-flash")
else:
    gemini = None


def resumir_tarea(materia: str, texto: str) -> str:
    """Usa Gemini para resumir el enunciado de una tarea en una línea."""
    if not gemini or not texto or len(texto) < 20:
        return texto
    try:
        prompt = (
            f"Eres un asistente escolar. Resume en UNA sola frase corta y clara "
            f"(máximo 15 palabras) qué hay que hacer para esta tarea de {materia}. "
            f"Solo di qué hay que hacer, sin saludos ni explicaciones extra.\n\n"
            f"Tarea: {texto}"
        )
        resp = gemini.generate_content(prompt)
        resumen = resp.text.strip().strip('"').strip("'")
        logger.info(f"Gemini resumió: {resumen}")
        return resumen
    except Exception as e:
        logger.warning(f"Error con Gemini: {e}")
        return texto


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
                    raise ValueError("Login fallido.")

                # ── PASO 3: Ir a Tareas ────────────────────────────────────
                try:
                    await page.wait_for_selector("text=Tareas", timeout=8000)
                    await page.get_by_text("Tareas").first.click()
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except PWTimeout:
                    for path in ["Ejercicios.aspx", "Alumno/Tareas.aspx"]:
                        try:
                            r = await page.goto(f"https://comunicacion.esemtia.ec/{path}", timeout=10000)
                            if r and r.status == 200:
                                break
                        except Exception:
                            continue

                logger.info(f"[Tareas] URL: {page.url}")
                return await self._extraer_con_expansion(page, dias)

            except ValueError:
                raise
            except Exception as e:
                logger.error(f"Error: {e}")
                raise
            finally:
                await browser.close()

    async def _extraer_con_expansion(self, page, dias: int) -> list[dict]:
        hoy    = datetime.now().date()
        limite = hoy + timedelta(days=dias)
        tareas = []
        vistas = set()

        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        filas_validas = []
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

                clave = f"{materia}|{titulo[:20]}"
                if clave in vistas:
                    continue
                vistas.add(clave)

                fecha_entrega = self._parsear_fecha(fecha_texto)
                if not fecha_entrega or not (hoy <= fecha_entrega <= limite):
                    continue

                filas_validas.append({
                    "fecha":   fecha_entrega,
                    "materia": materia,
                    "titulo":  titulo,
                    "fila_id": fila.get("id", ""),
                })

        # Expandir cada fila y leer contenido completo
        for item in filas_validas:
            titulo_completo = item["titulo"]
            descripcion = ""

            if item["fila_id"]:
                try:
                    fila_elem = page.locator(f"#{item['fila_id']}")
                    await fila_elem.click(timeout=5000)
                    await page.wait_for_timeout(1000)

                    html_exp = await page.content()
                    soup_exp = BeautifulSoup(html_exp, "html.parser")

                    content_id = item["fila_id"].replace("tarea_", "tareaContent_").replace("row_", "detail_")
                    content_div = soup_exp.find(id=content_id)

                    if content_div:
                        texto = content_div.get_text(" ", strip=True)
                        if texto and len(texto) > len(titulo_completo):
                            lineas = [l.strip() for l in texto.split("\n") if l.strip()]
                            if lineas:
                                titulo_completo = lineas[0]
                                descripcion = " ".join(lineas[1:])[:500] if len(lineas) > 1 else ""
                    else:
                        fila_sig = soup_exp.find(id=item["fila_id"])
                        if fila_sig:
                            sig = fila_sig.find_next_sibling()
                            if sig:
                                texto = sig.get_text(" ", strip=True)
                                if texto and len(texto) > 10:
                                    descripcion = texto[:500]

                    # Resumir con Gemini el texto completo
                    texto_completo = f"{titulo_completo} {descripcion}".strip()
                    titulo_resumido = resumir_tarea(item["materia"], texto_completo)
                    titulo_completo = titulo_resumido

                    await fila_elem.click(timeout=3000)
                    await page.wait_for_timeout(500)

                except Exception as ex:
                    logger.warning(f"No pude expandir fila {item['fila_id']}: {ex}")

            tareas.append({
                "fecha":       item["fecha"],
                "materia":     item["materia"],
                "titulo":      titulo_completo,
                "descripcion": "",
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
