import os
import json
from collections import defaultdict
from typing import Any, DefaultDict, Dict, Hashable, Tuple

from telegram.ext import BasePersistence

from supabase import create_client, Client


# -----------------------------
# Ключи, по которым храним данные
# -----------------------------
def _k_user(uid: int) -> str:
    return f"user_data:{uid}"

def _k_chat(cid: int) -> str:
    return f"chat_data:{cid}"

def _k_bot() -> str:
    return "bot_data"

def _k_conversations(name: str) -> str:
    return f"conversations:{name}"

def _k_callback() -> str:
    return "callback_data"


class SupabasePersistence(BasePersistence):
    """
    PTB 22.4 совместимая реализация Persistence для Supabase.
    Хранит:
      - user_data:*          -> dict
      - chat_data:*          -> dict
      - bot_data             -> dict
      - conversations:*      -> dict[(tuple(chat_id, user_id) | ...)] -> state
      - callback_data        -> dict
    """
    def __init__(
        self,
        url: str,
        key: str,
        table: str = "bot_state",
        *,
        store_user_data: bool = True,
        store_chat_data: bool = True,
        store_bot_data: bool = True,
        on_flush: bool = True,
    ):
        # В PTB BasePersistence эти флаги нужно явно передать в super()
        # Сохраним флаги в своих атрибутах, а super вызовем без них
        self.store_user_data = store_user_data
        self.store_chat_data = store_chat_data
        self.store_bot_data  = store_bot_data
        super().__init__(update_interval=0, on_flush=on_flush)
        
        self.client: Client = create_client(url, key)
        self.table = table

        # Локальный кэш, чтобы не дергать БД каждый раз
        self._user_data: DefaultDict[int, Dict[str, Any]] = defaultdict(dict)
        self._chat_data: DefaultDict[int, Dict[str, Any]] = defaultdict(dict)
        self._bot_data: Dict[str, Any] = {}
        self._conversations: Dict[str, Dict[Tuple[Hashable, ...], Any]] = defaultdict(dict)
        self._callback_data: Dict[str, Any] = {}

        self._loaded = False

    # ---------- ВСПОМОГАТЕЛЬНЫЕ ----------
    def _get_row(self, key: str) -> Dict[str, Any] | None:
        res = self.client.table(self.table).select("data").eq("id", key).execute()
        if res.data:
            return res.data[0]["data"]
        return None

    def _upsert_row(self, key: str, data: Any) -> None:
        self.client.table(self.table).upsert({"id": key, "data": data}).execute()

    def _delete_row(self, key: str) -> None:
        self.client.table(self.table).delete().eq("id", key).execute()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        # Ничего «массово» не грузим — лениво тянем по ключам, когда это понадобится.
        self._loaded = True

    # ---------- Требуемые абстрактные методы PTB ----------

    # ---- user_data ----
    async def get_user_data(self) -> DefaultDict[int, Dict[str, Any]]:
        self._ensure_loaded()
        return self._user_data

    async def update_user_data(self, user_id: int, data: Dict[str, Any]) -> None:
        self._ensure_loaded()
        self._user_data[user_id] = data or {}
        if self.store_user_data:
            self._upsert_row(_k_user(user_id), self._user_data[user_id])

    async def drop_user_data(self, user_id: int) -> None:
        self._ensure_loaded()
        self._user_data.pop(user_id, None)
        if self.store_user_data:
            self._delete_row(_k_user(user_id))

    async def refresh_user_data(self) -> None:
        # Перезагрузка из БД (лениво: подгружаем только те user_id, что уже в кэше)
        for uid in list(self._user_data.keys()):
            row = self._get_row(_k_user(uid))
            self._user_data[uid] = row or {}

    # ---- chat_data ----
    async def get_chat_data(self) -> DefaultDict[int, Dict[str, Any]]:
        self._ensure_loaded()
        return self._chat_data

    async def update_chat_data(self, chat_id: int, data: Dict[str, Any]) -> None:
        self._ensure_loaded()
        self._chat_data[chat_id] = data or {}
        if self.store_chat_data:
            self._upsert_row(_k_chat(chat_id), self._chat_data[chat_id])

    async def drop_chat_data(self, chat_id: int) -> None:
        self._ensure_loaded()
        self._chat_data.pop(chat_id, None)
        if self.store_chat_data:
            self._delete_row(_k_chat(chat_id))

    async def refresh_chat_data(self) -> None:
        for cid in list(self._chat_data.keys()):
            row = self._get_row(_k_chat(cid))
            self._chat_data[cid] = row or {}

    # ---- bot_data ----
    async def get_bot_data(self) -> Dict[str, Any]:
        self._ensure_loaded()
        if not self._bot_data and self.store_bot_data:
            row = self._get_row(_k_bot())
            if isinstance(row, dict):
                self._bot_data = row
        return self._bot_data

    async def update_bot_data(self, data: Dict[str, Any]) -> None:
        self._ensure_loaded()
        self._bot_data = data or {}
        if self.store_bot_data:
            self._upsert_row(_k_bot(), self._bot_data)

    async def refresh_bot_data(self) -> None:
        row = self._get_row(_k_bot())
        self._bot_data = row or {}

    # ---- conversations ----
    async def get_conversations(self, name: str) -> Dict[Tuple[Hashable, ...], Any]:
        self._ensure_loaded()
        # Ленивая загрузка набора по имени
        if name not in self._conversations:
            row = self._get_row(_k_conversations(name))
            if isinstance(row, dict):
                # ключи в JSON — строки, превращаем обратно в tuples
                restored: Dict[Tuple[Hashable, ...], Any] = {}
                for k, v in row.items():
                    restored[tuple(json.loads(k))] = v
                self._conversations[name] = restored
            else:
                self._conversations[name] = {}
        return self._conversations[name]

    async def update_conversation(self, name: str, key: Tuple[Hashable, ...], new_state: Any) -> None:
        self._ensure_loaded()
        conv = await self.get_conversations(name)
        if new_state is None:
            conv.pop(key, None)
        else:
            conv[key] = new_state

        # сериализуем tuple-ключ в строку (JSON-массив)
        to_store: Dict[str, Any] = {json.dumps(list(k)): v for k, v in conv.items()}
        self._upsert_row(_k_conversations(name), to_store)

    # ---- callback_data ----
    async def get_callback_data(self) -> Dict[str, Any]:
        self._ensure_loaded()
        if not self._callback_data:
            row = self._get_row(_k_callback())
            if isinstance(row, dict):
                self._callback_data = row
        return self._callback_data

    async def update_callback_data(self, data: Dict[str, Any]) -> None:
        self._ensure_loaded()
        self._callback_data = data or {}
        self._upsert_row(_k_callback(), self._callback_data)

    # ---- flush ----
    async def flush(self) -> None:
        # Принудительно записать всё, что в кэше, в БД
        if self.store_user_data:
            for uid, data in self._user_data.items():
                self._upsert_row(_k_user(uid), data or {})
        if self.store_chat_data:
            for cid, data in self._chat_data.items():
                self._upsert_row(_k_chat(cid), data or {})
        if self.store_bot_data:
            self._upsert_row(_k_bot(), self._bot_data or {})
        # conversations
        for name, conv in self._conversations.items():
            to_store: Dict[str, Any] = {json.dumps(list(k)): v for k, v in conv.items()}
            self._upsert_row(_k_conversations(name), to_store)
        # callback
        if self._callback_data is not None:
            self._upsert_row(_k_callback(), self._callback_data or {})
