"""SQLite 数据层：只依赖标准库，简单可靠，单文件便于备份。"""
import json
import os
import sqlite3
from datetime import datetime

from config import DB_PATH, COVER_DIR


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = connect()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS movies (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            code          TEXT NOT NULL UNIQUE,
            title         TEXT NOT NULL DEFAULT '',
            actress       TEXT NOT NULL DEFAULT '[]',
            release_date  TEXT NOT NULL DEFAULT '',
            added_at      TEXT NOT NULL DEFAULT '',
            resolution    TEXT NOT NULL DEFAULT '',
            is_original   INTEGER NOT NULL DEFAULT 0,
            is_remux      INTEGER NOT NULL DEFAULT 0,
            framerate     TEXT NOT NULL DEFAULT '',
            cover_path    TEXT NOT NULL DEFAULT '',
            download_url  TEXT NOT NULL DEFAULT '',
            source        TEXT NOT NULL DEFAULT '',
            note          TEXT NOT NULL DEFAULT '',
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def _row_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    try:
        d["actress"] = json.loads(d.get("actress") or "[]")
    except (ValueError, TypeError):
        d["actress"] = []
    return d


def get_movie(movie_id):
    conn = connect()
    row = conn.execute("SELECT * FROM movies WHERE id=?", (movie_id,)).fetchone()
    conn.close()
    return _row_to_dict(row)


def get_by_code(code):
    conn = connect()
    row = conn.execute("SELECT * FROM movies WHERE code=?", (code,)).fetchone()
    conn.close()
    return _row_to_dict(row)


def list_movies(date_field="added_at", date_from="", date_to="", actress="", keyword=""):
    """按日期区间 / 演员 / 关键字筛选。date_field 仅允许 release_date 或 added_at。"""
    if date_field not in ("release_date", "added_at"):
        date_field = "added_at"
    clauses, args = [], []

    if date_from:
        clauses.append(f"{date_field} >= ?")
        args.append(date_from)
    if date_to:
        clauses.append(f"{date_field} <= ?")
        args.append(date_to)
    if actress:
        # actress 存 JSON 数组文本，用 LIKE 做模糊包含匹配
        clauses.append("actress LIKE ?")
        args.append(f"%{actress}%")
    if keyword:
        clauses.append("(code LIKE ? OR title LIKE ?)")
        args.extend([f"%{keyword}%", f"%{keyword}%"])

    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    # 有日期筛选时按该日期排序，否则按录入时间倒序
    order_field = date_field if (date_from or date_to) else "added_at"
    sql = f"SELECT * FROM movies{where} ORDER BY {order_field} DESC, id DESC"
    conn = connect()
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def all_actresses():
    conn = connect()
    rows = conn.execute("SELECT actress FROM movies").fetchall()
    conn.close()
    names = set()
    for r in rows:
        try:
            for n in json.loads(r["actress"] or "[]"):
                if n:
                    names.add(n)
        except (ValueError, TypeError):
            continue
    return sorted(names)


def _normalize(data):
    """把表单/接口传入的 dict 归一化成入库字段。"""
    actresses = data.get("actress", [])
    if isinstance(actresses, str):
        # 支持逗号/顿号分隔的手动输入
        actresses = [a.strip() for a in actresses.replace("，", ",").split(",") if a.strip()]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "code": str(data.get("code", "")).strip().upper(),
        "title": str(data.get("title", "")).strip(),
        "actress": json.dumps(actresses, ensure_ascii=False),
        "release_date": str(data.get("release_date", "")).strip(),
        "added_at": str(data.get("added_at", "")).strip() or now[:10],
        "resolution": str(data.get("resolution", "")).strip(),
        "is_original": 1 if str(data.get("is_original", "0")) in ("1", "true", "on", "True") else 0,
        "is_remux": 1 if str(data.get("is_remux", "0")) in ("1", "true", "on", "True") else 0,
        "framerate": str(data.get("framerate", "")).strip(),
        "cover_path": str(data.get("cover_path", "")).strip(),
        "download_url": str(data.get("download_url", "")).strip(),
        "source": str(data.get("source", "")).strip(),
        "note": str(data.get("note", "")).strip(),
        "now": now,
    }


def create_movie(data):
    d = _normalize(data)
    if not d["code"]:
        raise ValueError("编号不能为空")
    conn = connect()
    try:
        conn.execute(
            """INSERT INTO movies
               (code,title,actress,release_date,added_at,resolution,is_original,is_remux,
                framerate,cover_path,download_url,source,note,created_at,updated_at)
               VALUES (:code,:title,:actress,:release_date,:added_at,:resolution,:is_original,:is_remux,
                :framerate,:cover_path,:download_url,:source,:note,:now,:now)""",
            d,
        )
        conn.commit()
        movie_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError(f"编号 {d['code']} 已存在")
    conn.close()
    return get_movie(movie_id)


def update_movie(movie_id, data):
    d = _normalize(data)
    conn = connect()
    conn.execute(
        """UPDATE movies SET
             code=:code,title=:title,actress=:actress,release_date=:release_date,added_at=:added_at,
             resolution=:resolution,is_original=:is_original,is_remux=:is_remux,framerate=:framerate,
             cover_path=:cover_path,download_url=:download_url,source=:source,note=:note,updated_at=:now
           WHERE id=:id""",
        {**d, "id": movie_id},
    )
    conn.commit()
    conn.close()
    return get_movie(movie_id)


def delete_movie(movie_id):
    """删除记录，同时清理其本地封面文件。"""
    movie = get_movie(movie_id)
    if movie and movie.get("cover_path"):
        path = os.path.join(COVER_DIR, movie["cover_path"])
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass
    conn = connect()
    conn.execute("DELETE FROM movies WHERE id=?", (movie_id,))
    conn.commit()
    conn.close()
