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
            encabezado = f"🟡 *En {dias_falta} días — {fecha.strftime('%A %d/%m')}*"

        lineas.append(encabezado)
        for t in items:
            lineas.append(f"  • *{t['materia']}*: {t['titulo']}")
            if t.get("descripcion"):
                lineas.append(f"    _{t['descripcion']}_")
        lineas.append("")

    lineas.append("—\n_Bot de deberes 🤖 · Esemtia_")
    return "\n".join(lineas)


async def revisar_y_notificar(bot: Bot):
    logger.info("⏰ Revisando deberes en Esemtia...")
    try:
        scraper = EsemtiaScraper(ESEMTIA_USER, ESEMTIA_PASS)
        tareas  = await scraper.obtener_tareas_proximas(dias=7)
        mensaje = formatear_mensaje(tareas)
        await bot.send_message(chat_id=CHAT_ID, text=mensaje, parse_mode="Markdown")
        logger.info("✅ Notificación enviada.")
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
        "/deberes — Ver deberes ahora mismo\n"
        f"/start   — Este mensaje\n\n"
        f"⏰ Aviso automático todos los días a las {HORA_AVISO}"
    )

async def cmd_deberes(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Revisando Esemtia, dame un momento...")
    await revisar_y_notificar(context.bot)


async def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("deberes", cmd_deberes))

    hora, minuto = map(int, HORA_AVISO.split(":"))
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(revisar_y_notificar, "cron", hour=hora, minute=minuto, args=[app.bot])
    scheduler.start()
    logger.info(f"✅ Bot activo — aviso diario a las {HORA_AVISO} ({ZONA_HORARIA})")

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
