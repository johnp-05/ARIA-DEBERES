import asyncio
import logging
import os
from datetime import datetime, timedelta

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot
from telegram.ext import Application, CommandHandler, ContextTypes

from scraper import EsemtiaScraper

# ─── Configuración ────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID        = os.environ["CHAT_ID"]
ESEMTIA_USER   = os.environ["ESEMTIA_USER"]
ESEMTIA_PASS   = os.environ["ESEMTIA_PASS"]
HORA_AVISO     = "14:30"
ZONA_HORARIA   = "America/Guayaquil"
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
tz = pytz.timezone(ZONA_HORARIA)

DIAS_SEMANA = {
    "Monday": "Lunes", "Tuesday": "Martes", "Wednesday": "Miércoles",
    "Thursday": "Jueves", "Friday": "Viernes", "Saturday": "Sábado", "Sunday": "Domingo"
}


def formatear_mensaje(tareas: list[dict]) -> str:
    hoy    = datetime.now(tz).date()
    manana = hoy + timedelta(days=1)

    if not tareas:
        return "✅ *No hay deberes registrados* para los próximos días.\n_¡A descansar! 🎉_"

    por_fecha: dict = {}
    for t in tareas:
        por_fecha.setdefault(t["fecha"], []).append(t)

    lineas = ["📚 *DEBERES PENDIENTES*\n"]
    for fecha, items in sorted(por_fecha.items()):
        if fecha == hoy:
            encabezado = "⚠️ *PARA HOY*"
        elif fecha == manana:
            encabezado = "🔴 *PARA MAÑANA*"
        else:
            dias_falta = (fecha - hoy).days
            dia_es = DIAS_SEMANA.get(fecha.strftime("%A"), fecha.strftime("%A"))
            encabezado = f"🟡 *En {dias_falta} días — {dia_es} {fecha.strftime('%d/%m')}*"

        lineas.append(encabezado)
        for t in items:
            lineas.append(f"  • *{t['materia']}*: {t['titulo']}")
        lineas.append("")

    lineas.append("—\n_Bot de deberes 🤖 · Esemtia_")
    return "\n".join(lineas)


def formatear_whatsapp(tareas: list[dict]) -> str:
    """Formato limpio para copiar y pegar en WhatsApp."""
    hoy    = datetime.now(tz).date()
    manana = hoy + timedelta(days=1)

    if not tareas:
        return "✅ No hay deberes para los próximos días."

    por_fecha: dict = {}
    for t in tareas:
        por_fecha.setdefault(t["fecha"], []).append(t)

    lineas = ["📚 *DEBERES PENDIENTES*\n"]
    for fecha, items in sorted(por_fecha.items()):
        if fecha == hoy:
            encabezado = "⚠️ PARA HOY"
        elif fecha == manana:
            encabezado = "🔴 PARA MAÑANA"
        else:
            dias_falta = (fecha - hoy).days
            dia_es = DIAS_SEMANA.get(fecha.strftime("%A"), fecha.strftime("%A"))
            encabezado = f"🟡 {dia_es} {fecha.strftime('%d/%m')} (en {dias_falta} días)"

        lineas.append(encabezado)
        for t in items:
            # Materia sin el horario (ej: "QUIMICA 12:05" → "QUIMICA")
            materia_limpia = t['materia'].split(" ")[0] if t['materia'] else t['materia']
            lineas.append(f"• {materia_limpia}: {t['titulo']}")
        lineas.append("")

    return "\n".join(lineas).strip()


async def revisar_y_notificar(bot: Bot):
    logger.info("Revisando deberes en Esemtia...")
    try:
        scraper = EsemtiaScraper(ESEMTIA_USER, ESEMTIA_PASS)
        tareas  = await scraper.obtener_tareas_proximas(dias=7)
        mensaje = formatear_mensaje(tareas)
        await bot.send_message(chat_id=CHAT_ID, text=mensaje, parse_mode="Markdown")
        logger.info("Notificacion enviada.")
    except Exception as e:
        logger.error(f"Error: {e}")
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"⚠️ Error revisando Esemtia:\n`{e}`",
            parse_mode="Markdown",
        )


async def cmd_start(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 ¡Hola! Soy tu bot de deberes.\n\n"
        "/deberes  — Ver deberes ahora mismo\n"
        "/whatsapp — Mensaje listo para copiar al grupo\n"
        f"/start    — Este mensaje\n\n"
        f"⏰ Aviso automático todos los días a las {HORA_AVISO}"
    )


async def cmd_deberes(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Revisando Esemtia, dame un momento...")
    await revisar_y_notificar(context.bot)


async def cmd_whatsapp(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Revisando Esemtia, dame un momento...")
    try:
        scraper = EsemtiaScraper(ESEMTIA_USER, ESEMTIA_PASS)
        tareas  = await scraper.obtener_tareas_proximas(dias=7)
        mensaje = formatear_whatsapp(tareas)
        # Enviar en bloque de código para que sea fácil copiar
        await update.message.reply_text(
            f"📋 *Copia este mensaje para WhatsApp:*\n\n`{mensaje}`",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"⚠️ Error: `{e}`", parse_mode="Markdown")


async def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("deberes",  cmd_deberes))
    app.add_handler(CommandHandler("whatsapp", cmd_whatsapp))

    hora, minuto = map(int, HORA_AVISO.split(":"))
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(revisar_y_notificar, "cron", hour=hora, minute=minuto, args=[app.bot])
    scheduler.start()
    logger.info(f"Bot activo — aviso diario a las {HORA_AVISO} ({ZONA_HORARIA})")

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
