"""影片管理库：Flask 单服务（页面 + API + 抓取 + 静态封面）。"""
import functools
import os

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


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "未登录"}), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        if request.form.get("password", "") == config.APP_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
        error = "密码错误"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    date_field = request.args.get("date_field", "added_at")
    return render_template(
        "index.html",
        date_field=date_field,
        date_from=request.args.get("date_from", ""),
        date_to=request.args.get("date_to", ""),
        actress=request.args.get("actress", ""),
        keyword=request.args.get("keyword", ""),
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
    # 抓取到的每个演员都给出候选名：中文名优先，含别名组里的其他写法
    result["actress_options"] = [
        {"original": name, "candidates": scraper.actress_candidates(name, aliases)}
        for name in result.get("actress", [])
    ]
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
    return render_template("detail.html", movie=movie)


@app.route("/covers/<path:filename>")
@login_required
def cover(filename):
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
