import os
import logging
from fastapi import FastAPI, Request, HTTPException
from telegram import Update

from telegram.ext import Application, CommandHandler, MessageHandler, filters
from app.handlers import start, echo

from contextlib import asynccontextmanager

# Переменные окружения (добавим позже на Render и локально)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "jslkdji&8987812kjkj9989l_lki")  # временно


DEBUG = os.getenv("DEBUG", "false").lower() in {"1", "true", "yes", "on"}
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- старт приложения ---
    if not TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN is empty — set it in env")
        raise RuntimeError("No TELEGRAM_BOT_TOKEN")

    tg_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # ⬇️ Регистрируем хэндлеры PTB
    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    app.state.tg_app = tg_app
    log.info("PTB Application created")

    # Переходим к работе приложения
    yield

    # --- остановка приложения ---
    tg_app = getattr(app.state, "tg_app", None)
    if tg_app:
        await tg_app.shutdown()
        log.info("PTB Application shut down")

app = FastAPI(lifespan=lifespan)

@app.get("/healthz")
async def healthz():
    log.debug("Health check requested")
    return {"status": "ok"}

@app.post("/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request):
    if secret != WEBHOOK_SECRET:
        log.warning("Webhook with wrong secret")
        raise HTTPException(status_code=403, detail="bad secret")
     # 1) Читаем JSON из запроса
    data = await request.json()

    # 2) Превращаем JSON в объект Update (это PTB-шный класс)
    update = Update.de_json(data, app.state.tg_app.bot)

    # 3) Кладём Update в очередь PTB
    await app.state.tg_app.update_queue.put(update)

    log.debug("Update forwarded to PTB")
    return {"ok": True}

