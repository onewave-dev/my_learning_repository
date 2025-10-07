# app/supabase_persistence.py

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any, DefaultDict, Dict, Hashable, Tuple, Optional

from supabase import create_client, Client
from telegram.ext import BasePersistence, PersistenceInput, _utils

log = logging.getLogger("app.supabase")


def _conv_key_encode(key: Tuple[Hashable, Hashable]) -> str:
    """
    Сериализация ключа разговора (chat_id, thread_id) -> строка.
    PTB использует tuple[chat_id, thread_id] как ключ в conversations.
    """
    chat_id, thread_id = key
    return f"{chat_id}:{thread_id if thread_id is not None else ''}"


def _conv_key_decode(s: str) -> Tuple[Hashable, Hashable]:
    """Обратная операция для _conv_key_encode."""
    if ":" not in s:
        return (int(s), None)
    chat_str, thread_str = s.split(":", 1)
    chat_id = int(chat_str) if chat_str else None
    thread_id = int(thread_str) if thread_str else None if thread_str != "" else None
    return (chat_id, thread_id)


class SupabasePersistence(BasePersistence):
    """
    Persistence для python-telegram-bot, сохраняющий состояние в Supabase.

    В таблице `bot_state` держим 5 строк:
      - <prefix>:user_data
      - <prefix>:chat_data
      - <prefix>:bot_data
      - <prefix>:conversations
      - <prefix>:callback_data

    Где `data` — это JSON.
    """

    def __init__(
        self,
        supabase_url: str,
        supabase_key: str,
        table: str = "bot_state",
        prefix: str = "main",
        store_data: Optional[PersistenceInput] = None,
        flush_on_update: bool = True,
    ) -> None:
        super().__init__(store_data=store_data)
        self.client: Client = create_client(supabase_url, supabase_key)
        self.table: str = table
        self.prefix: str = prefix
        self.flush_on_update: bool = flush_on_update

        # Локальные кэши (как в DictPersistence)
        self._user_data: DefaultDict[int, Dict[str, Any]] = defaultdict(dict)
        self._chat_data: DefaultDict[int, Dict[str, Any]] = defaultdict(dict)
        self._bot_data: Dict[str, Any] = {}
        self._conversations: Dict[str, Dict[Tuple[Hashable, Hashable], Any]] = {}
        self._callback_data: Dict[str, Any] = {}

        # Загружаем состояние один раз при инициализации
        self._load_all()

    # ---------- Публичные вспомогательные методы ----------

    async def health_check(self) -> None:
        """
        Fail-fast проверка доступности Supabase и таблицы.
        Выполняет select + upsert тестовой записи и удаляет её.
        """
        # 1) лёгкий SELECT
        try:
            _ = self.client.table(self.table).select("id").limit(1).execute()
        except Exception as e:
            raise RuntimeError(f"Cannot select from table '{self.table}': {e}")

        # 2) пробный round-trip
        probe_id = f"{self.prefix}:__healthcheck__"
        try:
            self.client.table(self.table).upsert({"id": probe_id, "data": {"ok": True}}).execute()
            got = self.client.table(self.table).select("data").eq("id", probe_id).execute()
            if not got.data:
                raise RuntimeError("Upsert succeeded but select returned no data")
        except Exception as e:
            raise RuntimeError(f"Cannot upsert/select in '{self.table}': {e}")
        finally:
            try:
                self.client.table(self.table).delete().eq("id", probe_id).execute()
            except Exception:
                logging.getLogger("app.handlers").warning("Healthcheck cleanup failed", exc_info=True)

    # ---------- Реализация обязательных методов BasePersistence ----------

    def get_user_data(self) -> DefaultDict[int, Dict[str, Any]]:
        return self._user_data

    def get_chat_data(self) -> DefaultDict[int, Dict[str, Any]]:
        return self._chat_data

    def get_bot_data(self) -> Dict[str, Any]:
        return self._bot_data

    def get_callback_data(self) -> Optional[Dict[str, Any]]:
        # PTB допускает None, если callback_data не используется
        return self._callback_data

    def get_conversations(self, name: str) -> Dict[Tuple[Hashable, Hashable], Any]:
        return self._conversations.get(name, {})
    

    # --- update* вызываются PTB после каждого изменения данных ---

    def update_user_data(self, user_id: int, data: Dict[str, Any]) -> None:
        if not self.store_data.user_data:
            return
        self._user_data[user_id] = data
        if self.flush_on_update:
            self.flush()

    def update_chat_data(self, chat_id: int, data: Dict[str, Any]) -> None:
        if not self.store_data.chat_data:
            return
        self._chat_data[chat_id] = data
        if self.flush_on_update:
            self.flush()

    def update_bot_data(self, data: Dict[str, Any]) -> None:
        if not self.store_data.bot_data:
            return
        self._bot_data = data
        if self.flush_on_update:
            self.flush()
            
    def refresh_bot_data(self, bot_data: Dict[str, Any]) -> None:
        self._bot_data = bot_data or {}
        if self.flush_on_update:
            self.flush()

    def update_callback_data(self, data: Dict[str, Any]) -> None:
        if not self.store_data.callback_data:
            return
        self._callback_data = data
        if self.flush_on_update:
            self.flush()

    def update_conversation(
        self,
        name: str,
        key: Tuple[Hashable, Hashable],
        new_state: Any,
    ) -> None:
        if not self.store_data.conversations:
            return
        conv = self._conversations.setdefault(name, {})
        if new_state is None:
            # PTB convention: remove conversation when state is None
            conv.pop(key, None)
        else:
            conv[key] = new_state
        if self.flush_on_update:
            self.flush()

    def drop_user_data(self, user_id: int) -> None:
        self._user_data.pop(user_id, None)
        if self.flush_on_update:
            self.flush()

    def drop_chat_data(self, chat_id: int) -> None:
        self._chat_data.pop(chat_id, None)
        if self.flush_on_update:
            self.flush()

    def refresh_user_data(self, user_id: int, user_data: Dict[str, Any]) -> None:
        # Современные PTB обычно не зовут это часто; поддержим для совместимости
        self._user_data[user_id] = user_data
        if self.flush_on_update:
            self.flush()

    def refresh_chat_data(self, chat_id: int, chat_data: Dict[str, Any]) -> None:
        self._chat_data[chat_id] = chat_data
        if self.flush_on_update:
            self.flush()

    def flush(self) -> None:
        """Сохраняем весь срез данных в Supabase одним upsert."""
        rows = [
            {"id": f"{self.prefix}:user_data", "data": self._user_data},
            {"id": f"{self.prefix}:chat_data", "data": self._chat_data},
            {"id": f"{self.prefix}:bot_data", "data": self._bot_data},
            {
                "id": f"{self.prefix}:conversations",
                "data": self._conversations_encode(self._conversations),
            },
            {"id": f"{self.prefix}:callback_data", "data": self._callback_data},
        ]
        try:
            self.client.table(self.table).upsert(rows).execute()
        except Exception:
            # Никогда не роняем приложение из-за временных проблем, пусть верхний уровень решает ретраи
            log.exception("Supabase upsert failed in flush()")

    # ---------- Приватные методы загрузки/сериализации ----------

    def _load_all(self) -> None:
        """Ленивая загрузка всех пяти сегментов из таблицы."""
        ids = [
            f"{self.prefix}:user_data",
            f"{self.prefix}:chat_data",
            f"{self.prefix}:bot_data",
            f"{self.prefix}:conversations",
            f"{self.prefix}:callback_data",
        ]
        try:
            resp = self.client.table(self.table).select("id, data").in_("id", ids).execute()
        except Exception:
            log.exception("Supabase select failed in _load_all()")
            # оставим пустые структуры
            return

        data_map: Dict[str, Any] = {row["id"]: row.get("data") for row in (resp.data or [])}

        # user_data
        ud = data_map.get(f"{self.prefix}:user_data") or {}
        if isinstance(ud, dict):
            # defaultdict(int->dict)
            self._user_data = defaultdict(dict, {int(k): v for k, v in ud.items()})
        else:
            self._user_data = defaultdict(dict)

        # chat_data
        cd = data_map.get(f"{self.prefix}:chat_data") or {}
        if isinstance(cd, dict):
            self._chat_data = defaultdict(dict, {int(k): v for k, v in cd.items()})
        else:
            self._chat_data = defaultdict(dict)

        # bot_data
        bd = data_map.get(f"{self.prefix}:bot_data") or {}
        if isinstance(bd, dict):
            self._bot_data = bd
        else:
            self._bot_data = {}

        # conversations
        conv = data_map.get(f"{self.prefix}:conversations") or {}
        if isinstance(conv, dict):
            self._conversations = self._conversations_decode(conv)
        else:
            self._conversations = {}

        # callback_data
        cb = data_map.get(f"{self.prefix}:callback_data") or {}
        if isinstance(cb, dict):
            self._callback_data = cb
        else:
            self._callback_data = {}

    @staticmethod
    def _conversations_encode(
        conv: Dict[str, Dict[Tuple[Hashable, Hashable], Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Превращаем ключи (chat_id, thread_id) в строки для JSON.
        """
        out: Dict[str, Dict[str, Any]] = {}
        for name, mapping in conv.items():
            encoded: Dict[str, Any] = {}
            for key_tuple, state in mapping.items():
                encoded[_conv_key_encode(key_tuple)] = state
            out[name] = encoded
        return out

    @staticmethod
    def _conversations_decode(
        data: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Dict[Tuple[Hashable, Hashable], Any]]:
        out: Dict[str, Dict[Tuple[Hashable, Hashable], Any]] = {}
        for name, mapping in data.items():
            decoded: Dict[Tuple[Hashable, Hashable], Any] = {}
            for key_str, state in (mapping or {}).items():
                decoded[_conv_key_decode(key_str)] = state
            out[name] = decoded
        return out
    