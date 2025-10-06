import os
import logging
import json
import asyncio

from fastapi import FastAPI, Request, HTTPException
from telegram import Update
from telegram.ext import (Application, CommandHandler, MessageHandler,
    filters, ConversationHandler, BasePersistence, CallbackQueryHandler,
    TypeHandler
)
from supabase import create_client
from app.handlers import (
    start, echo, help_command,
    survey_start, survey_name, survey_cancel, ASK_NAME, whoami,
    settings_command, settings_callback, error_handler,
    unknown_command, non_text, global_throttle
)
from contextlib import asynccontextmanager

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
DATA_PATH = os.getenv("DATA_PATH", "/var/data")


DEBUG = os.getenv("DEBUG", "false").lower() in {"1", "true", "yes", "on"}
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# Приглушаем болтливые логи библиотек, чтобы не светить токен
logging.getLogger("telegram").setLevel(logging.DEBUG)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)         # сетевые запросы
logging.getLogger("app.handlers").setLevel(logging.DEBUG)
log = logging.getLogger("app")

# устновка persistence
class SupabasePersistence(BasePersistence):
    def __init__(self, url, key):
        super().__init__(store_user_data=True, store_chat_data=True, store_bot_data=True)
        self.client = create_client(url, key)
        self.table = "bot_state"

    async def _load(self, key):
        res = self.client.table(self.table).select("data").eq("id", key).execute()
        if res.data:
            return res.data[0]["data"]
        return {}

    async def _save(self, key, data):
        self.client.table(self.table).upsert({"id": key, "data": data}).execute()

    async def get_user_data(self):
        return await self._load("user_data")

    async def update_user_data(self, user_id, data):
        await self._save(f"user_{user_id}", data)

    async def get_chat_data(self):
        return await self._load("chat_data")

    async def update_chat_data(self, chat_id, data):
        await self._save(f"chat_{chat_id}", data)

    async def get_bot_data(self):
        return await self._load("bot_data")

    async def update_bot_data(self, data):
        await self._save("bot_data", data)

    async def flush(self):
        # метод для PTB (здесь ничего не нужно)
        pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- старт приложения ---
    if not TELEGRAM_BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN is empty — set it in env")
        raise RuntimeError("No TELEGRAM_BOT_TOKEN")
    
    # 🔹 гарантируем, что путь существует (работает в рантайме на диске)
    os.makedirs(DATA_PATH, exist_ok=True)


    # 🔹 файл состояния на диске
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY") 
    persistence = SupabasePersistence(SUPABASE_URL, SUPABASE_KEY)
    
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
    # await app.state.tg_app.update_queue.put(update)

    # 3a) ✅ Напрямую передаём апдейт в PTB (минуя очередь)
    await app.state.tg_app.process_update(update)

    log.debug("Update forwarded to PTB")
    return {"ok": True}

