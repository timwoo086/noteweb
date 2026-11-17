#!/usr/bin/env python3
"""稳健推送到 GitHub：优先走本地代理用 git push；代理不可用则自动降级到
GitHub Contents API 逐文件同步，并同步 tag / release。

用法：
    python push_github.py                 # 推送当前 main
    python push_github.py v0.0.19 "说明"  # 推送并创建该版本 tag + release

网络策略（按顺序尝试）：
  1. 若 127.0.0.1:7897 代理在线 -> git push（走代理，最快）
  2. 否则 git push 直连试 2 次
  3. 仍失败 -> GitHub Contents API 逐文件 PUT（已验证稳定）
凭据：从 git credential fill 读取（Windows 凭据管理器里的 PAT）。
"""
import base64
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = "timwoo086/noteweb"
BRANCH = "main"
PROXY = os.environ.get("GH_PROXY", "http://127.0.0.1:7897")
TIMEOUT = 30


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def token():
    p = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                       capture_output=True, text=True)
    for line in p.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    return os.environ.get("GH_TOKEN", "")


def proxy_alive(host="127.0.0.1", port=7897):
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def api(path, method="GET", body=None, tk=""):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("https://api.github.com" + path, data=data, method=method,
        headers={"Authorization": "Bearer " + tk, "Accept": "application/vnd.github+json",
                 "Content-Type": " application/json", "User-Agent": "noteweb-push"})
    req.headers["Content-Type"] = "application/json"
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def git_push(tk):
    """按代理可用性构造 git 命令推送。"""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    if proxy_alive():
        cfg = ["-c", "http.proxy=" + PROXY, "-c", "https.proxy=" + PROXY]
        print("[git] 走代理 " + PROXY)
    else:
        cfg = ["-c", "http.proxy=", "-c", "https.proxy="]
        print("[git] 代理不可用，尝试直连")
    cmd = ["git"] + cfg + ["-c", "http.sslBackend=openssl", "-c", "http.sslVerify=false",
                           "push", "origin", BRANCH]
    for i in range(1, 3):
        r = run(cmd, env=env)
        if r.returncode == 0:
            print("[git] 推送成功")
            return True
        print("[git] 第 %d 次失败: %s" % (i, (r.stderr or "").strip().splitlines()[-1][:90]))
    return False


def api_push(tk):
    """降级：用 Contents API 逐文件同步工作区相对仓库根目录的所有跟踪文件。"""
    print("[api] 降级到 GitHub Contents API 同步")
    tracked = run(["git", "ls-files"]).stdout.splitlines()
    changed = run(["git", "diff", "--name-only", "HEAD"]).stdout.splitlines()
    targets = sorted(set(tracked) | set(changed))
    ok = 0
    for f in targets:
        if not os.path.isfile(f):
            continue
        url = "/repos/%s/contents/%s" % (REPO, f)
        try:
            cur = json.loads(api(url, tk=tk).read())
            sha = cur.get("sha")
        except urllib.error.HTTPError:
            sha = None
        body = {"message": "sync: 稳健推送脚本同步",
                "content": base64.b64encode(open(f, "rb").read()).decode(), "branch": BRANCH}
        if sha:
            body["sha"] = sha
        try:
            api(url, "PUT", body, tk)
            ok += 1
        except urllib.error.HTTPError as e:
            print("[api] 失败 %s: %s" % (f, e.code))
    print("[api] 已同步 %d 个文件" % ok)
    return ok


def make_release(tk, version, note):
    if not version:
        return
    sha = json.loads(api("/repos/%s/git/ref/heads/%s" % (REPO, BRANCH), tk=tk).read())["object"]["sha"]
    tag = "v" + version.lstrip("v")
    try:
        api("/repos/%s/git/ref/tags/%s" % (REPO, tag), tk=tk)
        print("[release] tag 已存在: " + tag)
    except urllib.error.HTTPError:
        obj = json.loads(api("/repos/%s/git/tags" % REPO, "POST", {
            "tag": tag, "message": tag + " - " + note, "object": sha, "type": "commit",
            "tagger": {"name": "timwoo086", "email": "timwoo086@users.noreply.github.com",
                       "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        }, tk).read())
        api("/repos/%s/git/refs" % REPO, "POST", {"ref": "refs/tags/" + tag, "sha": obj["sha"]}, tk)
        print("[release] tag 已创建: " + tag)
    try:
        api("/repos/%s/releases" % REPO, "POST", {
            "tag_name": tag, "name": tag + " — " + note,
            "body": "提交：`" + sha[:7] + "`\n\n" + note,
            "draft": False, "prerelease": False}, tk)
        print("[release] Release 已创建")
    except urllib.error.HTTPError as e:
        print("[release] Release 创建失败: %s" % e.code)


def main():
    tk = token()
    if not tk:
        print("错误：找不到 GitHub token"); sys.exit(1)
    version = sys.argv[1] if len(sys.argv) > 1 else ""
    note = sys.argv[2] if len(sys.argv) > 2 else "同步更新"
    if not git_push(tk):
        api_push(tk)
    if version:
        make_release(tk, version, note)
    print("完成。本地 HEAD:", run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip())


if __name__ == "__main__":
    main()
