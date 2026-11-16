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


@app.route("/api/scrape", methods=["POST"])
@login_required
def api_scrape():
    data = request.get_json(silent=True) or {}
    result = scraper.scrape(data.get("code", ""))
    return jsonify({"ok": True, "data": result})


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


@app.route("/covers/<path:filename>")
@login_required
def cover(filename):
    return send_from_directory(config.COVER_DIR, filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.PORT, debug=False)
