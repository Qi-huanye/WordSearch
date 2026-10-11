import csv
import json
import os
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .native import protect, unprotect

DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WordSearch"


@dataclass
class Profile:
    name: str = "DeepSeek"
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-flash"
    key: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class Settings:
    profiles: list = field(default_factory=lambda: [Profile()])
    selected_id: str = ""
    lookup_hotkey: str = "Ctrl+D"
    select_hotkey: str = "Ctrl+Shift+D"
    region_hotkey: str = "Ctrl+Alt+D"
    region: list | None = None
    font_size: int = 14
    auto_query: bool = False
    auto_translate: bool = True
    hover_lookup: bool = True
    crop_width: int = 1100
    crop_height: int = 600

    @property
    def profile(self):
        return next((p for p in self.profiles if p.id == self.selected_id), self.profiles[0])


class Store:
    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.path = DATA_DIR / "settings.json"
        self.db = sqlite3.connect(DATA_DIR / "history.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY, term TEXT NOT NULL, context TEXT NOT NULL,
            answer TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
            created TEXT NOT NULL, favorite INTEGER NOT NULL DEFAULT 0)""")
        if "mode" not in {row["name"] for row in self.db.execute("PRAGMA table_info(history)")}:
            self.db.execute("ALTER TABLE history ADD COLUMN mode TEXT NOT NULL DEFAULT ''")
            self.db.execute("UPDATE history SET mode=CASE WHEN provider='ECDICT' THEN 'dictionary' WHEN term='' THEN 'translate' ELSE 'history' END")
        self.db.commit()
        self.notice = ""

    def load(self):
        if not self.path.exists():
            return Settings()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if "hover_lookup" not in data:
                data["auto_query"] = False
            profiles = []
            for p in data.pop("profiles", []):
                encrypted = p.pop("encrypted_key", "")
                p.pop("key", None)
                try:
                    key = unprotect(encrypted)
                except (ValueError, OSError):
                    key = ""
                    self.notice = "已读取设置；API Key 无法解密，请重新填写。"
                profiles.append(Profile(**p, key=key))
            fields = Settings.__dataclass_fields__
            return Settings(profiles=profiles or [Profile()], **{k: v for k, v in data.items() if k in fields})
        except (ValueError, OSError, TypeError):
            self.notice = "设置文件无法读取，已使用默认设置；原文件保留。"
            return Settings()

    def save(self, settings):
        data = asdict(settings)
        for p in data["profiles"]:
            p["encrypted_key"] = protect(p.pop("key"))
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def add(self, term, context, answer, profile, mode="dictionary"):
        result = self.db.execute("INSERT INTO history(term,context,answer,provider,model,created,mode) VALUES(?,?,?,?,?,?,?)",
                                 (term, context, answer, profile.name, profile.model,
                                  datetime.now().astimezone().isoformat(timespec="seconds"), mode))
        self.db.commit()
        return result.lastrowid

    def history(self, query="", only_favorites=False):
        return self.db.execute("""SELECT * FROM history WHERE (term LIKE ? OR context LIKE ?)
            AND (?=0 OR favorite=1) ORDER BY id DESC""",
            (f"%{query}%", f"%{query}%", int(only_favorites))).fetchall()

    def record(self, record_id):
        return self.db.execute("SELECT * FROM history WHERE id=?", (record_id,)).fetchone()

    def favorite(self, record_id, value):
        self.db.execute("UPDATE history SET favorite=? WHERE id=?", (int(value), record_id))
        self.db.commit()

    def delete(self, record_id=None):
        if record_id is None:
            self.db.execute("DELETE FROM history")
        else:
            self.db.execute("DELETE FROM history WHERE id=?", (record_id,))
        self.db.commit()

    def export(self, path, rows):
        with open(path, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["单词", "上下文", "释义", "服务", "模型", "时间", "收藏"])
            for row in rows:
                # Excel must treat game text as data rather than formulas.
                values = [row[k] for k in ("term", "context", "answer", "provider", "model", "created", "favorite")]
                writer.writerow(["'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v for v in values])
