"""SQLite 数据层：只依赖标准库，简单可靠，单文件便于备份。"""
import json
import os
import sqlite3
from datetime import datetime

from werkzeug.security import generate_password_hash, check_password_hash

from config import DB_PATH, COVER_DIR, APP_PASSWORD

# 演员别名配置：JSON 文件，结构为 [{"name": 规范名, "aliases": [别名1, 别名2]}, ...]
# 同一组内的名字视为同一个演员，点击任一名字都会筛出整组影片。
ALIASES_PATH = os.path.join(os.path.dirname(DB_PATH), "aliases.json")


def _aliases_path():
    return ALIASES_PATH


def load_aliases():
    """读取别名分组配置，返回列表。"""
    try:
        with open(ALIASES_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (OSError, ValueError):
        pass
    return []


def save_aliases(groups):
    """保存别名分组配置。"""
    os.makedirs(os.path.dirname(ALIASES_PATH), exist_ok=True)
    with open(ALIASES_PATH, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)
    return groups


def _canonical(name):
    """返回某名字所属组的规范名（不在任何组则返回自身）。"""
    for g in load_aliases():
        members = [g.get("name", "")] + list(g.get("aliases", []))
        if name in members:
            return g.get("name", name)
    return name


def _group_members(name):
    """返回某名字所属组的全部名字（含自身），用于筛选。"""
    for g in load_aliases():
        members = [g.get("name", "")] + list(g.get("aliases", []))
        if name in members:
            return [m for m in members if m]
    return [name]


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
            is_bluray     INTEGER NOT NULL DEFAULT 0,
            is_remux      INTEGER NOT NULL DEFAULT 0,
            framerate     TEXT NOT NULL DEFAULT '',
            cover_path    TEXT NOT NULL DEFAULT '',
            cover_url     TEXT NOT NULL DEFAULT '',
            download_url  TEXT NOT NULL DEFAULT '',
            source        TEXT NOT NULL DEFAULT '',
            note          TEXT NOT NULL DEFAULT '',
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )
        """
    )
    # 向后兼容：旧库没有 cover_url 列时补上
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(movies)").fetchall()}
    if "cover_url" not in cols:
        conn.execute("ALTER TABLE movies ADD COLUMN cover_url TEXT NOT NULL DEFAULT ''")
    if "is_bluray" not in cols:
        conn.execute("ALTER TABLE movies ADD COLUMN is_bluray INTEGER NOT NULL DEFAULT 0")

    # 多账号：用户名 + 密码哈希，last_date 记录该账号最近操作的日期分组
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            username      TEXT PRIMARY KEY COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            is_admin      INTEGER NOT NULL DEFAULT 0,
            last_date     TEXT NOT NULL DEFAULT '',
            last_movie_id INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )
        """
    )
    # 首次升级时用原 APP_PASSWORD 建立管理员账号，老密码可继续登录
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO users (username,password_hash,is_admin,created_at,updated_at) VALUES (?,?,?,?,?)",
            ("admin", generate_password_hash(APP_PASSWORD), 1, now, now),
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
        # 展开别名组：同组内任一名字命中即算该演员（如 RION / りおん）
        members = _group_members(actress)
        sub = " OR ".join("actress LIKE ?" for _ in members)
        clauses.append(f"({sub})")
        args.extend(f"%{m}%" for m in members)
    if keyword:
        clauses.append("(code LIKE ? OR title LIKE ?)")
        args.extend([f"%{keyword}%", f"%{keyword}%"])

    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    # 始终按当前日期模式倒序，最新日期排最上面；同日按 id 倒序
    sql = f"SELECT * FROM movies{where} ORDER BY {date_field} DESC, id DESC"
    conn = connect()
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def all_actresses():
    """演员列表：同一别名组合并为规范名，避免下拉里重复。"""
    conn = connect()
    rows = conn.execute("SELECT actress FROM movies").fetchall()
    conn.close()
    names = set()
    for r in rows:
        try:
            for n in json.loads(r["actress"] or "[]"):
                if n:
                    names.add(_canonical(n))
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
        "is_bluray": 1 if str(data.get("is_bluray", "0")) in ("1", "true", "on", "True") else 0,
        "is_remux": 1 if str(data.get("is_remux", "0")) in ("1", "true", "on", "True") else 0,
        "framerate": str(data.get("framerate", "")).strip(),
        "cover_path": str(data.get("cover_path", "")).strip(),
        "cover_url": str(data.get("cover_url", "")).strip(),
        "download_url": str(data.get("download_url", "")).strip(),
        "source": str(data.get("source", "")).strip(),
        "note": str(data.get("note", "")).strip(),
        "now": now,
    }


_INSERT_SQL = """INSERT INTO movies
   (code,title,actress,release_date,added_at,resolution,is_original,is_bluray,is_remux,
    framerate,cover_path,cover_url,download_url,source,note,created_at,updated_at)
   VALUES (:code,:title,:actress,:release_date,:added_at,:resolution,:is_original,:is_bluray,:is_remux,
    :framerate,:cover_path,:cover_url,:download_url,:source,:note,:now,:now)"""


def _insert_movie(conn, d):
    conn.execute(_INSERT_SQL, d)
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def create_movie(data):
    d = _normalize(data)
    if not d["code"]:
        raise ValueError("编号不能为空")
    conn = connect()
    try:
        movie_id = _insert_movie(conn, d)
    except sqlite3.IntegrityError:
        # 洗版共存：备注含「洗版」时同番号允许多条并存，自动追加 -R2/-R3… 后缀
        if "洗版" not in d["note"]:
            conn.close()
            raise ValueError(f"编号 {d['code']} 已存在")
        base, movie_id, n = d["code"], None, 2
        while n <= 99:
            d["code"] = f"{base}-R{n}"
            try:
                movie_id = _insert_movie(conn, d)
                break
            except sqlite3.IntegrityError:
                n += 1
        if movie_id is None:
            conn.close()
            raise ValueError(f"编号 {base} 洗版后缀已用尽（-R2 ~ -R99 均被占用）")
    conn.close()
    return get_movie(movie_id)


def update_movie(movie_id, data):
    d = _normalize(data)
    conn = connect()
    conn.execute(
        """UPDATE movies SET
             code=:code,title=:title,actress=:actress,release_date=:release_date,added_at=:added_at,
             resolution=:resolution,is_original=:is_original,is_bluray=:is_bluray,is_remux=:is_remux,framerate=:framerate,
             cover_path=:cover_path,cover_url=:cover_url,download_url=:download_url,source=:source,
             note=:note,updated_at=:now
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


def _user_to_dict(row):
    """返回可安全传给前端的用户信息，不带密码哈希。"""
    if row is None:
        return None
    return {
        "username": row["username"],
        "is_admin": bool(row["is_admin"]),
        "last_date": row["last_date"] or "",
        "last_movie_id": row["last_movie_id"] or 0,
    }


def get_user(username):
    conn = connect()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return _user_to_dict(row)


def verify_user(username, password):
    """账号 + 密码校验，成功返回用户信息（不含哈希）。"""
    conn = connect()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username or "",)).fetchone()
    conn.close()
    if row and check_password_hash(row["password_hash"], password or ""):
        return _user_to_dict(row)
    return None


def create_user(username, password, is_admin=False):
    """管理员建号。用户名唯一，密码至少 4 位。"""
    username = str(username or "").strip()
    password = str(password or "")
    if not username or any(ch.isspace() for ch in username):
        raise ValueError("账号不能为空且不能包含空格")
    if len(password) < 4:
        raise ValueError("密码至少 4 位")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    try:
        conn.execute(
            """INSERT INTO users (username,password_hash,is_admin,created_at,updated_at)
               VALUES (?,?,?,?,?)""",
            (username, generate_password_hash(password), 1 if is_admin else 0, now, now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError(f"账号 {username} 已存在")
    conn.close()
    return get_user(username)


def list_users():
    """管理员查看全部账号（不含密码哈希）。"""
    conn = connect()
    rows = conn.execute("SELECT * FROM users ORDER BY is_admin DESC, username").fetchall()
    conn.close()
    return [_user_to_dict(r) for r in rows]


def update_user_password(username, password):
    """管理员重置任意账号的登录密码。"""
    username = str(username or "").strip()
    password = str(password or "")
    if not get_user(username):
        raise ValueError(f"账号 {username} 不存在")
    if len(password) < 4:
        raise ValueError("密码至少 4 位")
    conn = connect()
    conn.execute(
        "UPDATE users SET password_hash=?, updated_at=? WHERE username=?",
        (generate_password_hash(password), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), username),
    )
    conn.commit()
    conn.close()
    return get_user(username)


def rename_actress(old_name, new_name):
    """把所有影片演员列表里的 old_name 统一改成 new_name（去重），返回修改的影片数。"""
    old_name = str(old_name or "").strip()
    new_name = str(new_name or "").strip()
    if not old_name:
        raise ValueError("要改的名字不能为空")
    if not new_name:
        raise ValueError("新名字不能为空")
    if old_name == new_name:
        raise ValueError("新旧名字相同")
    conn = connect()
    rows = conn.execute("SELECT id, actress FROM movies").fetchall()
    changed = 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for row in rows:
        try:
            names = json.loads(row["actress"] or "[]")
        except (ValueError, TypeError):
            continue
        if old_name not in names:
            continue
        new_names = []
        for n in names:
            n2 = new_name if n == old_name else n
            if n2 not in new_names:
                new_names.append(n2)
        conn.execute(
            "UPDATE movies SET actress=?, updated_at=? WHERE id=?",
            (json.dumps(new_names, ensure_ascii=False), now, row["id"]),
        )
        changed += 1
    conn.commit()
    conn.close()
    return changed


def set_last_position(username, date_key, movie_id=0):
    """记住某账号最近操作的日期分组，用于首页红字「上次到这里」。"""
    date_key = str(date_key or "").strip()[:50]
    try:
        movie_id = int(movie_id or 0)
    except (TypeError, ValueError):
        movie_id = 0
    conn = connect()
    conn.execute(
        "UPDATE users SET last_date=?, last_movie_id=?, updated_at=? WHERE username=?",
        (date_key, movie_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), username),
    )
    conn.commit()
    conn.close()
