import os
import logging
from fastapi import FastAPI, Request, HTTPException
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler  
from app.handlers import start, echo, help_command, survey_start, survey_name, survey_cancel, ASK_NAME, whoami
from contextlib import asynccontextmanager

# Переменные окружения 
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "jslkdji&8987812kjkj9989l_lki")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")


DEBUG = os.getenv("DEBUG", "false").lower() in {"1", "true", "yes", "on"}
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# Приглушаем болтливые логи библиотек, чтобы не светить токен
logging.getLogger("telegram").setLevel(logging.WARNING)      # весь PTB
logging.getLogger("telegram.ext").setLevel(logging.WARNING)  # подсистема ext
logging.getLogger("httpx").setLevel(logging.WARNING)         # сетевые запросы
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
    tg_app.add_handler(CommandHandler("help", help_command))
    # Диалог: /survey -> спросить имя -> ответ -> завершить
    conv = ConversationHandler(
        entry_points=[CommandHandler("survey", survey_start)],  # точка входа по /survey
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, survey_name)],  # ждём текст имени
        },
        fallbacks=[CommandHandler("cancel", survey_cancel)],  # /cancel на любом шаге — выход
        allow_reentry=True,  # позволит заново зайти в диалог, даже если пользователь был в нём
    )
    tg_app.add_handler(conv)
    tg_app.add_handler(CommandHandler("whoami", whoami))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    app.state.tg_app = tg_app
    log.info("PTB Application created")

    # Переходим к работе приложения

    await tg_app.initialize()   # подготовить внутренние ресурсы PTB (сессии, луп и т.д.)
    await tg_app.start()        # запустить фоновые задачи PTB, чтобы обрабатывать update_queue
    log.info("PTB Application started")

    if not PUBLIC_URL:
        log.error("PUBLIC_URL is empty — set it in env")
        raise RuntimeError("No PUBLIC_URL")

    webhook_url = f"{PUBLIC_URL}/webhook"
    await tg_app.bot.set_webhook(
        url=webhook_url,
        secret_token=WEBHOOK_SECRET,      # Telegram пришлёт этот секрет в заголовке
        drop_pending_updates=True,        # не тянуть «старые» апдейты
        allowed_updates=["message"]       # на старте берём только сообщения
    )
    log.info("Webhook set to %s", webhook_url) 

    yield

    # --- остановка приложения ---
    tg_app = getattr(app.state, "tg_app", None)
    if tg_app:
        await tg_app.stop()       # остановить фоновые задачи (обработка очереди)
        await tg_app.shutdown()   # корректно освободить ресурсы
        log.info("PTB Application stopped")

app = FastAPI(lifespan=lifespan)

@app.get("/healthz")
async def healthz():
    log.debug("Health check requested")
    return {"status": "ok"}

@app.post("/webhook")
async def telegram_webhook(request: Request):  
    # проверка: заголовок от Telegram
    header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if header_secret != WEBHOOK_SECRET:
        log.warning("Webhook header secret mismatch")
        raise HTTPException(status_code=403, detail="bad header secret")
    log.debug("Header OK, update accepted")

    # 1) Читаем JSON из запроса
    data = await request.json()

    # 2) Превращаем JSON в объект Update (это PTB-шный класс)
    update = Update.de_json(data, app.state.tg_app.bot)

    # 3) Кладём Update в очередь PTB
    await app.state.tg_app.update_queue.put(update)

    log.debug("Update forwarded to PTB")
    return {"ok": True}

