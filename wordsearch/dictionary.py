import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Entry:
    word: str
    phonetic: str
    definition: str
    translation: str
    original: str = ""
    lemma: str = ""


@dataclass
class Definition:
    term: str
    entries: list[Entry]
    kind: str = ""

    def markdown(self):
        parts = []
        for index, item in enumerate(self.entries):
            title = item.word + (f" /{item.phonetic}/" if item.phonetic else "")
            if index and self.kind == "form":
                title += " · 词形参考"
            if item.original:
                title += f" · {item.original} 的原形"
            parts.append(f"### {title}\n\n{item.translation}\n\n{item.definition}")
        return "\n\n".join(parts)


class Dictionary:
    def __init__(self):
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        path = root / "assets" / "dictionary" / "ecdict.sqlite3"
        self.db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) if path.exists() else None
        if self.db:
            self.db.row_factory = sqlite3.Row

    def entry(self, term):
        if not self.db:
            return None
        normalized = term.strip().lower().replace("’", "'")
        row = self.db.execute("SELECT * FROM entries WHERE word=?", (normalized,)).fetchone()
        form = self.db.execute("SELECT lemma FROM forms WHERE form=?", (normalized,)).fetchone()
        if form and form["lemma"] != normalized:
            lemma = self.db.execute("SELECT * FROM entries WHERE word=?", (form["lemma"],)).fetchone()
            if lemma and (not row or not row["translation"]):
                return Entry(lemma["display_word"], lemma["phonetic"], self.clean(lemma["definition"]),
                             self.clean(lemma["translation"]), term)
        if row:
            return Entry(row["display_word"], row["phonetic"], self.clean(row["definition"]), self.clean(row["translation"]),
                         lemma=form["lemma"] if form and "0:" in row["exchange"] and form["lemma"] != normalized else "")
        if form:
            lemma = self.db.execute("SELECT * FROM entries WHERE word=?", (form["lemma"],)).fetchone()
            if lemma:
                return Entry(lemma["display_word"], lemma["phonetic"], self.clean(lemma["definition"]), self.clean(lemma["translation"]), term)
        return None

    @staticmethod
    def clean(text):
        return text.replace("\\n", "\n").strip()

    def lookup(self, term):
        exact = self.entry(term)
        if exact:
            if exact.lemma and (lemma := self.entry(exact.lemma)):
                return Definition(term, [exact, lemma], "form")
            return Definition(term, [exact])
        words = re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)*", term)
        entries = [entry for word in words[:8] if (entry := self.entry(word))]
        return Definition(term, entries, "phrase")
