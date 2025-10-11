import os
import logging
import tempfile
import asyncio

from fastapi import FastAPI, Request, HTTPException
from telegram import Update
from telegram.ext import (Application, CommandHandler, MessageHandler,
    filters, ConversationHandler, CallbackQueryHandler
)
from supabase import create_client, Client
from app.handlers import (
    start, echo, help_command,
    survey_start, survey_name, survey_cancel, ASK_NAME, whoami,
    settings_command, settings_callback, error_handler,
    unknown_command, non_text, global_throttle
)
from contextlib import asynccontextmanager
from app.supabase_persistence import SupabasePersistence
log = logging.getLogger("app")
# --- ВЕРХ ФАЙЛА (рядом с импортами) ---
def _probe(tag):
    async def _inner(update, context):
        msg = getattr(update, "effective_message", None)
        txt = getattr(msg, "text", None)
        log.warning("PROBE %s | has_message=%s | text=%r | entities=%s",
                    tag, bool(msg), txt,
                    getattr(msg, "entities", None))
    return _inner

# Переменные окружения 
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "jslkdji&8987812kjkj9989l_lki")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")


# ✅ Настройка уровня логирования
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO  # ← вместо DEBUG
)

# ✅ Отключаем болтливые библиотеки
for noisy in ["hpack", "httpcore", "httpx", "urllib3"]:
    logging.getLogger(noisy).setLevel(logging.WARNING)

# Приглушаем болтливые логи библиотек, чтобы не светить токен
logging.getLogger("telegram").setLevel(logging.INFO)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.INFO)
logging.getLogger("app.handlers").setLevel(logging.DEBUG)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- старт приложения ---
    if not TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN is empty — set it in env")
        raise RuntimeError("No TELEGRAM_BOT_TOKEN")

    # 🔹 файл состояния на диске
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY") 
    if not (SUPABASE_URL and SUPABASE_KEY):
        log.error("SUPABASE_URL/SUPABASE_KEY are required in env for persistence")
        raise RuntimeError("Supabase credentials are missing")

    # ✅ Только SupabasePersistence
    persistence = SupabasePersistence(SUPABASE_URL, SUPABASE_KEY)

    # 🔎 Fail-fast: проверяем доступ к БД/таблице прямо на старте
    try:
        await persistence.health_check()   # добавим метод ниже в классе
        log.info("Supabase health-check OK — running in Supabase-only mode")
    except Exception as e:
        log.exception("Supabase health-check FAILED: %s", e)
        raise RuntimeError(f"Supabase is not available: {e}")
    
    tg_app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .persistence(persistence)
        .build()
    )

    # ⬇️ Регистрируем хэндлеры PTB
    tg_app.add_handler(MessageHandler(filters.ALL, _probe("A:TOP"), block=False), group=0) # 0a. Зонд до всего (он НЕ блокирует)
    tg_app.add_handler(MessageHandler(filters.ALL, global_throttle, block=False), group=0) # всегда в самом начале
    # 0c. Зонд прямо перед командами (чтобы видеть, что до сюда дошло)
    tg_app.add_handler(MessageHandler(filters.ALL, _probe("B:BEFORE_CMDS"), block=False), group=0)
    tg_app.add_handler(CommandHandler("start", start))
    log.info("Start handler registered")
    tg_app.add_handler(CommandHandler("whoami", whoami))
    tg_app.add_handler(CommandHandler("help", help_command))
    tg_app.add_handler(CommandHandler("settings", settings_command))

    # 1b. Зонд после команд
    tg_app.add_handler(MessageHandler(filters.ALL, _probe("C:AFTER_CMDS"), block=False), group=0)
    tg_app.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^settings:"))
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
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    tg_app.add_handler(MessageHandler(~filters.TEXT & ~filters.COMMAND, non_text))     # всё, что не текст и не команды: фото, стикеры, файлы и т.д.
    tg_app.add_error_handler(error_handler)
    tg_app.add_handler(MessageHandler(filters.COMMAND, unknown_command))     # Этот хэндлер ДОЛЖЕН быть последним, чтобы не перехватывать известные команды
    app.state.tg_app = tg_app
    log.info("PTB Application created")

    # Переходим к работе приложения

    await tg_app.initialize()   # подготовить внутренние ресурсы PTB (сессии, луп и т.д.)
    await tg_app.start()        # запустить фоновые задачи PTB, чтобы обрабатывать update_queue
    log.info("PTB Application started")

    # Меню команд в клиенте Telegram
    await tg_app.bot.set_my_commands([
        ("start", "Поздороваться и увидеть счётчик запусков"),
        ("help", "Что умеет бот"),
        ("survey", "Мини-опрос: спросить имя"),
        ("settings", "Открыть настройки с кнопками"),
        ("whoami", "Показать сохранённое имя"),
    ])
    log.info("Bot commands are set")

    if not PUBLIC_URL:
        log.error("PUBLIC_URL is empty — set it in env")
        raise RuntimeError("No PUBLIC_URL")

    webhook_url = f"{PUBLIC_URL}/webhook"
    await tg_app.bot.set_webhook(
        url=webhook_url,
        secret_token=WEBHOOK_SECRET,      # Telegram пришлёт этот секрет в заголовке
        drop_pending_updates=True,        # не тянуть «старые» апдейты
        allowed_updates=["message", "callback_query"]
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
    # NEW: видим, что вообще до нас дошёл запрос и какие заголовки пришли
    log.info("Webhook hit: method=%s, headers=%s", request.method, dict(request.headers))

    header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if header_secret != WEBHOOK_SECRET:
        log.warning("Webhook header secret mismatch: got=%r expected=%r", header_secret, WEBHOOK_SECRET)
        raise HTTPException(status_code=403, detail="bad header secret")
    log.info("Webhook header OK")

    data = await request.json()
    update = Update.de_json(data, app.state.tg_app.bot)
    await app.state.tg_app.process_update(update)
    log.info("Update forwarded to PTB (ok)")
    return {"ok": True}

