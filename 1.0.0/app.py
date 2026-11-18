"""影片管理库：Flask 单服务（页面 + API + 抓取 + 静态封面）。"""
import functools
import os
import re
import threading
from collections import defaultdict

from flask import (
    Flask, jsonify, redirect, render_template, request,
    send_from_directory, session, url_for,
)

import config
import db
import scraper

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
db.init_db()

# 详情页封面压缩尺寸与静态资源版本号（模板用 ?v= 做缓存刷新）
DETAIL_COVER_MAX_WIDTH = 640
DETAIL_COVER_QUALITY = 82
THUMB_MAX_SIZE = (480, 480)   # 卡片缩略图最大边框，竖版封面等比缩放
THUMB_QUALITY = 75
_detail_cover_locks = defaultdict(threading.Lock)
_thumb_locks = defaultdict(threading.Lock)
STATIC_DIR = os.path.join(config.BASE_DIR, "static")
ASSET_VERSION = str(int(max(
    os.path.getmtime(os.path.join(STATIC_DIR, name))
    for name in ("style.css", "app.js", "detail.js")
)))


def ensure_thumb(filename):
    """把封面原图生成持久化缩略图（covers/thumbs/<名>.jpg）；已存在或无法处理返回 False。

    首次访问某卡片时按需生成一次，之后浏览器与磁盘双层缓存，列表页首屏大幅提速。
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        return False
    src = os.path.join(config.COVER_DIR, filename)
    if not os.path.isfile(src):
        return False
    thumb_dir = os.path.join(config.COVER_DIR, "thumbs")
    os.makedirs(thumb_dir, exist_ok=True)
    out_name = os.path.splitext(os.path.basename(filename))[0] + ".jpg"
    out = os.path.join(thumb_dir, out_name)

    def fresh():
        return os.path.isfile(out) and os.path.getmtime(out) >= os.path.getmtime(src)

    try:
        if fresh():
            return True
        with _thumb_locks[out_name]:
            if fresh():
                return True
            from PIL import Image
            tmp = out + ".tmp"
            with Image.open(src) as im:
                im = im.convert("RGB")
                im.thumbnail(THUMB_MAX_SIZE, Image.LANCZOS)
                im.save(tmp, "JPEG", quality=THUMB_QUALITY, optimize=True, progressive=True)
            # 原子替换：多 worker 并发生成同一缩略图也不会写坏文件
            os.replace(tmp, out)
        return os.path.isfile(out)
    except Exception:
        return False


def backfill_thumbs():
    """启动后台补齐存量封面的缩略图，不阻塞服务。"""
    import time as _time

    def _worker():
        try:
            for name in sorted(os.listdir(config.COVER_DIR)):
                path = os.path.join(config.COVER_DIR, name)
                if not os.path.isfile(path):
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext not in (".jpg", ".jpeg", ".png", ".webp"):
                    continue
                try:
                    if not ensure_thumb(name):
                        # 原图可能损坏，跳过并稍作停顿避免狂刷
                        _time.sleep(0.05)
                except Exception:
                    pass
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


# gunicorn 直接 import app:app，不执行 __main__；在导入时启动补齐（多 worker 各跑一份，幂等无害）
backfill_thumbs()


@app.context_processor
def inject_asset_version():
    return {"asset_ver": ASSET_VERSION}


@app.after_request
def add_cache_headers(response):
    path = request.path
    if path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path.startswith("/covers/") or path.startswith("/thumbs/"):
        # 仍需登录才能访问，只允许浏览器私有缓存，不进共享缓存/CDN
        response.headers["Cache-Control"] = "private, max-age=86400"
    return response


def current_user():
    """获取当前登录用户；会话失效时返回 None。"""
    username = session.get("username")
    return db.get_user(username) if username else None


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            session.pop("username", None)
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "未登录"}), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    """只允许管理员修改数据；普通账号只能查看、勾选和复制。"""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user or not user["is_admin"]:
            return jsonify({"ok": False, "error": "需要管理员权限"}), 403
        return view(*args, **kwargs)

    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        user = db.verify_user(
            request.form.get("username", "").strip(),
            request.form.get("password", ""),
        )
        if user:
            session["username"] = user["username"]
            return redirect(url_for("index"))
        error = "账号或密码错误"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.pop("username", None)
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    user = current_user()
    date_field = request.args.get("date_field", "added_at")
    # 默认进入 FT.A.V 栏目；只有明确传 category 时才切换（允许空值表示不筛选）
    category = request.args.get("category", None)
    if category is None:
        category = "FT.A.V"
    return render_template(
        "index.html",
        date_field=date_field,
        date_from=request.args.get("date_from", ""),
        date_to=request.args.get("date_to", ""),
        actress=request.args.get("actress", ""),
        keyword=request.args.get("keyword", ""),
        category=category,
        username=user["username"],
        is_admin=user["is_admin"],
        last_date=user["last_date"],
    )


@app.route("/api/movies")
@login_required
def api_movies():
    movies = db.list_movies(
        date_field=request.args.get("date_field", "added_at"),
        date_from=request.args.get("date_from", ""),
        date_to=request.args.get("date_to", ""),
        actress=request.args.get("actress", ""),
        keyword=request.args.get("keyword", ""),
        category=request.args.get("category", ""),
    )
    return jsonify({"ok": True, "movies": movies})


@app.route("/api/actresses")
@login_required
def api_actresses():
    return jsonify({"ok": True, "actresses": db.all_actresses()})


@app.route("/api/aliases", methods=["GET", "POST"])
@login_required
def api_aliases():
    """演员别名分组：同一人多个名字合并，点击任一名字筛出全部影片。"""
    if request.method == "POST":
        user = current_user()
        if not user or not user["is_admin"]:
            return jsonify({"ok": False, "error": "需要管理员权限"}), 403
        data = request.get_json(silent=True) or {}
        groups = data.get("aliases", [])
        db.save_aliases(groups)
        return jsonify({"ok": True, "aliases": groups})
    return jsonify({"ok": True, "aliases": db.load_aliases()})


@app.route("/api/scrape", methods=["POST"])
@login_required
def api_scrape():
    data = request.get_json(silent=True) or {}
    code = data.get("code", "")
    javdb_query = (data.get("javdb_id") or "").strip()
    if javdb_query:
        # 用户填的是 JavDB 影片番号：后台自动搜索匹配 → 详情页 ID → 抓标题/日期/封面/演员。
        # 兼容旧填法：/v/p4aG5、完整链接、纯 ID（p4aG5）。
        try:
            m = re.search(r"/v/([a-zA-Z0-9]{4,12})", javdb_query)
            if m:
                jid = m.group(1)
            elif re.fullmatch(r"[a-zA-Z0-9]{4,12}", javdb_query) and not javdb_query[0].isdigit():
                jid = javdb_query  # 纯 ID 形式（字母开头，如 p4aG5）
            else:
                # 番号：先查缓存（登录墙下也能用），未命中才实时搜索匹配
                jid = scraper._load_javdb_ids().get(javdb_query.upper()) or scraper._load_javdb_ids().get(code.upper())
                if not jid:
                    jid = scraper._search_javdb_id(javdb_query, code)  # 番号 → 自动匹配 ID
            if not jid:
                return jsonify({"ok": False, "error": "JavDB 未找到该番号对应的影片"}), 404
            scraper._remember_javdb_id(code, jid)
            result = scraper.fetch_javdb_detail(jid, javdb_query)
            result["code"] = code
            result["javdb_id"] = jid
            result["source"] = "javdb"
            result["errors"] = []
            aliases = db.load_aliases()
            all_names = result.get("actress_all", []) or result.get("actress", [])
            groups = []
            for name in result.get("actress", []):
                candidates = []
                for source_name in all_names:
                    for c in scraper.actress_candidates(source_name, aliases):
                        if c and c not in candidates:
                            candidates.append(c)
                candidates.sort(key=lambda n: (not scraper._is_chinese_name(n), n != name))
                groups.append({"original": name, "candidates": candidates})
            result["actress_options"] = groups
            return jsonify({"ok": True, "data": result})
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
            if "要求登录" in err or "登录" in err:
                err += "；稍后重试或在部署环境变量 JAVDB_COOKIES 填入浏览器 Cookie 可解"
            return jsonify({"ok": False, "error": f"JavDB 抓取失败: {err}"}), 400
    result = scraper.scrape(code)
    aliases = db.load_aliases()
    # 同一个演员的多个写法（javbus 日文名 + javdb 中文名）合并成一组候选，
    # 统一指向一个中文名，中文名排在首位。
    all_names = result.get("actress_all", []) or result.get("actress", [])
    groups = []
    for name in result.get("actress", []):
        candidates = []
        # 把映射到同一中文名的所有写法都收进来
        for source_name in all_names:
            for c in scraper.actress_candidates(source_name, aliases):
                if c and c not in candidates:
                    candidates.append(c)
        # 中文名优先，其余保持原顺序
        candidates.sort(key=lambda n: (not scraper._is_chinese_name(n), n != name))
        groups.append({"original": name, "candidates": candidates})
    result["actress_options"] = groups
    return jsonify({"ok": True, "data": result})


@app.route("/api/actress-candidates")
@login_required
def api_actress_candidates():
    """编辑影片时按当前演员名取候选，方便把旧数据手动改成中文名。"""
    name = request.args.get("name", "").strip()
    return jsonify({
        "ok": True,
        "name": name,
        "candidates": scraper.actress_candidates(name, db.load_aliases()) if name else [],
    })


@app.route("/api/actresses/rename", methods=["POST"])
@login_required
@admin_required
def api_rename_actress():
    """管理员：把所有影片里的旧演员名统一替换成新名字。"""
    data = request.get_json(silent=True) or {}
    try:
        changed = db.rename_actress(data.get("from", ""), data.get("to", ""))
        return jsonify({"ok": True, "changed": changed})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/movies", methods=["POST"])
@login_required
@admin_required
def api_create():
    data = request.get_json(silent=True) or {}
    try:
        movie = db.create_movie(data)
        # 有 JavDB ID 时写入缓存，之后刮削可直接走详情页绕过搜索墙
        if movie.get("javdb_id") and movie.get("code"):
            scraper._remember_javdb_id(movie["code"], movie["javdb_id"])
        return jsonify({"ok": True, "movie": movie})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/movies/<int:movie_id>", methods=["GET", "PUT", "DELETE"])
@login_required
def api_movie(movie_id):
    if request.method == "GET":
        movie = db.get_movie(movie_id)
        return jsonify({"ok": True, "movie": movie}) if movie else (jsonify({"ok": False}), 404)

    user = current_user()
    if not user or not user["is_admin"]:
        return jsonify({"ok": False, "error": "需要管理员权限"}), 403

    if request.method == "DELETE":
        db.delete_movie(movie_id)
        return jsonify({"ok": True})

    data = request.get_json(silent=True) or {}
    try:
        movie = db.update_movie(movie_id, data)
        if movie.get("javdb_id") and movie.get("code"):
            scraper._remember_javdb_id(movie["code"], movie["javdb_id"])
        return jsonify({"ok": True, "movie": movie})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/movie/<int:movie_id>")
@login_required
def movie_detail(movie_id):
    movie = db.get_movie(movie_id)
    if not movie:
        return redirect(url_for("index"))
    user = current_user()
    # 详情页也更新该账号的上次位置；日期由列表页链接带入，兼容旧链接则回退到录入日期
    date_key = request.args.get("date") or (movie.get("added_at") or "")[:10]
    db.set_last_position(user["username"], date_key, movie_id)
    return render_template("detail.html", movie=movie, is_admin=user["is_admin"])


@app.route("/api/users", methods=["GET", "POST"])
@login_required
@admin_required
def api_users():
    """管理员：查看全部账号 / 创建账号。"""
    if request.method == "GET":
        return jsonify({"ok": True, "users": db.list_users()})
    data = request.get_json(silent=True) or {}
    try:
        user = db.create_user(data.get("username", ""), data.get("password", ""))
        return jsonify({"ok": True, "user": user})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/users/<username>", methods=["PUT"])
@login_required
@admin_required
def api_update_user(username):
    """管理员：重置任意账号的登录密码。"""
    data = request.get_json(silent=True) or {}
    try:
        user = db.update_user_password(username, data.get("password", ""))
        return jsonify({"ok": True, "user": user})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/last-position", methods=["POST"])
@login_required
def api_last_position():
    """记录当前账号最近操作的日期分组，供首页显示红字。"""
    data = request.get_json(silent=True) or {}
    db.set_last_position(
        session["username"],
        data.get("date", ""),
        data.get("movie_id", 0),
    )
    return jsonify({"ok": True})


def optimized_detail_cover(filename):
    """生成详情页用的中等尺寸封面；失败时返回 None 由原图兜底。"""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        return None
    src = os.path.join(config.COVER_DIR, filename)
    if not os.path.isfile(src):
        return None
    detail_dir = os.path.join(config.COVER_DIR, "detail")
    os.makedirs(detail_dir, exist_ok=True)
    out_name = os.path.splitext(os.path.basename(filename))[0] + ".jpg"
    out = os.path.join(detail_dir, out_name)

    def fresh():
        return os.path.isfile(out) and os.path.getmtime(out) >= os.path.getmtime(src)

    try:
        if fresh():
            return out
        with _detail_cover_locks[out_name]:
            if fresh():
                return out
            from PIL import Image
            with Image.open(src) as im:
                im = im.convert("RGB")
                im.thumbnail((DETAIL_COVER_MAX_WIDTH, DETAIL_COVER_MAX_WIDTH))
                im.save(out, "JPEG", quality=DETAIL_COVER_QUALITY, optimize=True, progressive=True)
        return out if os.path.isfile(out) else None
    except Exception:
        return None


@app.route("/covers/<path:filename>")
@login_required
def cover(filename):
    target = optimized_detail_cover(filename)
    if target:
        return send_from_directory(os.path.dirname(target), os.path.basename(target))
    return send_from_directory(config.COVER_DIR, filename)


@app.route("/thumbs/<path:filename>")
@login_required
def thumb(filename):
    """卡片用缩略图：缺失时按需从原图生成并持久化，之后直接命中磁盘缓存；
    原图缺失或无法处理时回退原图，不影响显示。"""
    ensure_thumb(filename)
    name = os.path.splitext(filename)[0] + ".jpg"
    thumb_dir = os.path.join(config.COVER_DIR, "thumbs")
    if os.path.isfile(os.path.join(thumb_dir, name)):
        return send_from_directory(thumb_dir, name)
    return send_from_directory(config.COVER_DIR, filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.PORT, debug=False)
