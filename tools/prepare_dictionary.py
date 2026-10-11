"""Download the upstream ECDICT data and prepare the bundled offline index."""
import csv
import hashlib
import json
import sqlite3
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "assets" / "dictionary"
CACHE = ROOT / "build" / "dictionary-source"
REPOSITORY = "https://github.com/skywind3000/ECDICT"


def download(client, url, path):
    temporary = path.with_suffix(path.suffix + ".part")
    count = 0
    last_report = 0
    with client.stream("GET", url) as response:
        response.raise_for_status()
        with temporary.open("wb") as output:
            for block in response.iter_bytes(1024 * 1024):
                output.write(block)
                count += len(block)
                if count - last_report >= 20 * 1024 * 1024:
                    print(f"Downloaded {count // (1024 * 1024)} MB", flush=True)
                    last_report = count
    temporary.replace(path)


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        response = client.get("https://api.github.com/repos/skywind3000/ECDICT/commits/master")
        response.raise_for_status()
        revision = response.json()["sha"]
        source = CACHE / f"ecdict-{revision}.csv"
        base = f"https://raw.githubusercontent.com/skywind3000/ECDICT/{revision}"
        if not source.exists():
            print(f"Fetching ECDICT revision {revision}", flush=True)
            download(client, base + "/ecdict.csv", source)
        download(client, base + "/LICENSE", DATA / "ECDICT-LICENSE.txt")
    target = DATA / "ecdict.sqlite3"
    temporary = target.with_suffix(".building")
    if temporary.exists():
        temporary.unlink()
    db = sqlite3.connect(temporary)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    db.execute("CREATE TABLE entries (word TEXT PRIMARY KEY, display_word TEXT, phonetic TEXT, definition TEXT, translation TEXT, exchange TEXT) WITHOUT ROWID")
    db.execute("CREATE TABLE forms (form TEXT PRIMARY KEY, lemma TEXT) WITHOUT ROWID")
    rows, forms, count = [], [], 0
    with source.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"word", "phonetic", "definition", "translation", "exchange"}.issubset(reader.fieldnames or []):
            raise RuntimeError("Unexpected ECDICT CSV format")
        for row in reader:
            word = row["word"].strip()
            normalized = word.lower()
            if not normalized:
                continue
            rows.append((normalized, word, row["phonetic"], row["definition"], row["translation"], row["exchange"]))
            for item in row["exchange"].split("/"):
                kind, _, value = item.partition(":")
                if kind in ("p", "d", "i", "3", "s", "r", "t"):
                    forms.extend((form.strip().lower(), normalized) for form in value.split(",") if form.strip())
                elif kind == "0" and value:
                    forms.append((normalized, value.split(",")[0].strip().lower()))
            count += 1
            if len(rows) >= 5000:
                db.executemany("INSERT OR IGNORE INTO entries VALUES(?,?,?,?,?,?)", rows)
                db.executemany("INSERT OR IGNORE INTO forms VALUES(?,?)", forms)
                rows.clear()
                forms.clear()
        db.executemany("INSERT OR IGNORE INTO entries VALUES(?,?,?,?,?,?)", rows)
        db.executemany("INSERT OR IGNORE INTO forms VALUES(?,?)", forms)
    db.commit()
    actual_count = db.execute("SELECT count(*) FROM entries").fetchone()[0]
    db.close()
    temporary.replace(target)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    (DATA / "source.json").write_text(json.dumps({"name": "ECDICT", "repository": REPOSITORY,
        "revision": revision, "source_sha256": digest, "entries": actual_count,
        "license": "MIT", "data_file": "ecdict.csv"}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Prepared {actual_count:,} entries at {target}", flush=True)


if __name__ == "__main__":
    main()
