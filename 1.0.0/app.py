"""影片管理库：Flask 单服务（页面 + API + 抓取 + 静态封面）。"""
import functools
import os
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
_detail_cover_locks = defaultdict(threading.Lock)
STATIC_DIR = os.path.join(config.BASE_DIR, "static")
ASSET_VERSION = str(int(max(
    os.path.getmtime(os.path.join(STATIC_DIR, name))
    for name in ("style.css", "app.js", "detail.js")
)))


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
    return render_template(
        "index.html",
        date_field=date_field,
        date_from=request.args.get("date_from", ""),
        date_to=request.args.get("date_to", ""),
        actress=request.args.get("actress", ""),
        keyword=request.args.get("keyword", ""),
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
    result = scraper.scrape(data.get("code", ""))
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


@app.route("/api/movies", methods=["POST"])
@login_required
@admin_required
def api_create():
    data = request.get_json(silent=True) or {}
    try:
        movie = db.create_movie(data)
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


@app.route("/api/users", methods=["POST"])
@login_required
@admin_required
def api_create_user():
    """管理员创建账号。"""
    data = request.get_json(silent=True) or {}
    try:
        user = db.create_user(data.get("username", ""), data.get("password", ""))
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
    """卡片用缩略图；旧数据没有缩略图时回退到原图，不影响显示。"""
    name = os.path.splitext(filename)[0] + ".jpg"
    thumb_dir = os.path.join(config.COVER_DIR, "thumbs")
    if os.path.isfile(os.path.join(thumb_dir, name)):
        return send_from_directory(thumb_dir, name)
    return send_from_directory(config.COVER_DIR, filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.PORT, debug=False)
