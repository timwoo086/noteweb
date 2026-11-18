"""多站抓取解析器。

JavBus  : 编号直达详情页 https://www.javbus.com/<CODE>
MissAV  : https://missav123.com/<code小写>，标题/演员/日期/封面，需过 Cloudflare（curl_cffi 模拟浏览器指纹）
JavDB   : 先搜索 https://javdb.com/search?q=<CODE>&f=all 命中后进入详情页；搜索有登录墙，
          配置环境变量 JAVDB_COOKIES（浏览器复制的 Cookie）后可免登录抓取
AVBase  : https://www.avbase.net/works/<CODE>，补 JavDB/JavBus 缺标题和演员
DMM     : https://www.dmm.co.jp/...，按番号推断 CID，作为标题/演员/封面兜底

设计要点：
1. 选择器集中放在顶部常量列表，站点改版时只改这一处；每个字段配多个候选选择器容错。
2. 多站并行抓取，结果合并：以先成功者为主，缺失字段用其他站补齐。
3. 封面下载到本地 data/covers，避免防盗链导致外链失效。
4. 任何站点失败都不阻断整体流程，返回已抓到的字段 + 错误说明，用户仍可手动补全。
"""
import concurrent.futures
import json
import os
import re
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from config import (
    COVER_DIR, HTTP_PROXY, JAVDB_COOKIES, JAVDB_IDS_PATH,
    REQUEST_TIMEOUT, RETRY,
    FLARESOLVERR_URL, FLARESOLVERR_TIMEOUT, FLARESOLVERR_CACHE_TTL,
)

JAVBUS_BASE = "https://www.javbus.com"
JAVDB_BASE = "https://javdb.com"
MISSAV_BASE = "https://missav123.com"
AVBASE_BASE = "https://www.avbase.net"
DMM_BASE = "https://www.dmm.co.jp"

# curl_cffi 可选：能模拟浏览器 TLS 指纹，过一部分 Cloudflare 拦截
try:
    from curl_cffi import requests as _creq
except ImportError:  # pragma: no cover
    _creq = None

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,ja;q=0.8,en;q=0.7",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1",
}

# 演员名归一：使用 avdb 完整映射表（actress_names.py），旧的常用映射保留为补充
import actress_names


def to_chinese(name):
    """把演员名转成规范中文名（avdb 映射表 + 繁简兜底）。"""
    return actress_names.to_cn(name)


def actress_candidates(name, aliases=None):
    """返回同一演员的所有写法，中文名优先；用户自定义分组优先级最高。"""
    return actress_names.candidates(name, aliases)


def _is_chinese_name(name):
    return actress_names.is_chinese(name)


# ---------- 选择器表（改版时主要维护这里）----------
# 标题
TITLE_SELECTORS = [
    # JavDB 详情页标题（current-title 为番号+标题正文）
    ".current-title",
    "h2.title",
    "h1",
    "h3",
    "div.info h3",
    ".container h3",
    ".video-info h2",
    ".page-detail h1",
    "strong.title",
]
# 演员链接
ACTRESS_SELECTORS = [
    '.info a[href*="/star/"]',
    '.info a[href*="/actor/"]',
    'a[href*="/star/"]',
    '.video-info a[href*="/actors/"]',
    'a[href*="/actor/"]',
    # DMM 常见演员链接
    'a[href*="/article/actress/"]',
    '.info dd a[href*="/article/actress/"]',
    # AVBase 出演者链接
    'a[href*="/talents/"]',
]
# 发行日期：JavBus 常见 "发行日期：" 后跟 <a>；JavDB 常见 "日期："
DATE_LABELS = ["发行日期", "発売日", "日期", "上映日期", "release date", "配信開始日"]
DATE_SELECTORS = [
    '.info a[href*="/date/"]',
    '.info span.genre + a',
    '.video-info .value',
    'a[href*="/works/date/"]',   # AVBase 的発売日链接
]
# 封面（顺序即优先级，按站点改版时维护这里）
COVER_SELECTORS = [
    'a.bigImage img',                       # JavBus 大图链接
    '.video-cover img',                     # JavDB 详情封面
    '#cover img',
    '.cover img',
    '.cover-wrap img',
    'img[src*="/pics/"]',                   # JavBus 图片 CDN
    'img[src*="pl.jpg"]',                   # DMM 大图
    'img[src*="ps.jpg"]',
    'img[src*="cover"]',
    'img[src*="awsimgsrc"]',                # AVBase 常用放大图字段
    'meta[property="og:image"]',            # 兜底：社交分享图，通常是高清封面
    'meta[name="twitter:image"]',
]

DATE_RE = re.compile(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})")

# MissAV 详情页的元数据标签（繁体），按标签取值比选择器稳
MISSAV_LABELS = ("發行日期", "番號", "標題", "女優")


def _proxies():
    """显式返回代理 dict；空串时用空 dict 禁用系统环境里的代理，保证真正直连。"""
    if HTTP_PROXY:
        return {"http": HTTP_PROXY, "https": HTTP_PROXY}
    return {"http": None, "https": None}


# 共享 Session：trust_env=False 屏蔽系统环境变量里的代理，
# 代理只认 config.HTTP_PROXY，避免 ambient 环境变量劫持请求
_session = requests.Session()
_session.trust_env = False


def _is_cf_challenge(resp):
    """判断响应是否是 Cloudflare 人机验证 / JS Challenge 页面。"""
    if resp is None:
        return False
    if getattr(resp, "status_code", 0) in (403, 429, 503):
        text = (getattr(resp, "text", "") or "")[:6000].lower()
        markers = (
            "just a moment", "cf-browser-verification", "cf-challenge",
            "checking your browser", "enable javascript and cookies",
            "attention required", "cloudflare", "ray id",
        )
        if any(m in text for m in markers):
            return True
    return False


# FlareSolverr 会话与 clearance 缓存：同一域名求解一次后可复用 cookie，避免频繁触发验证
_fs_session_id = ""
_fs_clearance = {}   # domain -> {"cookies": [...], "ua": str, "ts": float}
_fs_lock = __import__("threading").Lock()


def _fs_solve(url):
    """调用 FlareSolverr 求解 Cloudflare 挑战，返回 (html, cookies_dict, user_agent)。

    未配置 FLARESOLVERR_URL 或求解失败时返回 None。
    """
    if not FLARESOLVERR_URL:
        return None
    global _fs_session_id
    try:
        with _fs_lock:
            if not _fs_session_id:
                r = requests.post(
                    FLARESOLVERR_URL + "/v1",
                    json={"cmd": "sessions.create", "session": "noteweb"},
                    timeout=20, proxies=_proxies(),
                )
                data = r.json() if r.ok else {}
                # 已存在时直接复用
                sid = data.get("session") or "noteweb"
                _fs_session_id = sid
            payload = {
                "cmd": "request.get", "url": url,
                "maxTimeout": FLARESOLVERR_TIMEOUT * 1000,
                "session": _fs_session_id,
            }
        r = requests.post(FLARESOLVERR_URL + "/v1", json=payload,
                          timeout=FLARESOLVERR_TIMEOUT + 15, proxies=_proxies())
        if not r.ok:
            return None
        data = r.json()
        if data.get("status") != "ok" or not data.get("solution"):
            return None
        sol = data["solution"]
        cookies = {}
        for c in sol.get("cookies") or []:
            if c.get("name"):
                cookies[c["name"]] = c.get("value", "")
        return sol.get("response") or "", cookies, sol.get("userAgent") or ""
    except Exception:
        # 求解失败不影响原流程，由调用方按原样报错
        return None


def _flaresolverr_get(url, cookies=None):
    """通过 FlareSolverr 拿页面；命中缓存的 clearance 时直接复用其 cookie。

    返回类 requests.Response 的轻量对象（含 text/status_code/url），供现有解析逻辑使用。
    """
    if not FLARESOLVERR_URL:
        return None
    domain = (urlparse(url).hostname or "").lower()
    now = time.time()
    cached = _fs_clearance.get(domain)
    use_cookies = dict(cookies or {})
    ua = None
    if cached and now - cached["ts"] < FLARESOLVERR_CACHE_TTL:
        use_cookies.update(cached["cookies"])
        ua = cached["ua"]

    solved = _fs_solve(url)
    if solved is None:
        return None
    html, fs_cookies, fs_ua = solved
    if fs_cookies:
        use_cookies.update(fs_cookies)
        _fs_clearance[domain] = {"cookies": fs_cookies, "ua": fs_ua, "ts": now}

    class _Resp:
        def __init__(self, _text, _url):
            self.status_code = 200
            self.text = _text
            self.url = _url
            self.headers = {}

    return _Resp(html, url)


def _impersonate_get(url, cookies=None):
    """带浏览器 TLS 指纹的 GET（curl_cffi），用于有 Cloudflare 的站点。

    curl_cffi 缺失或代理协议不兼容时回退到普通 requests；
    若返回结果仍是 CF 挑战页，且配置了 FlareSolverr，则自动改走 FlareSolverr 求解。
    """
    resp = None
    if _creq is not None:
        try:
            # 4xx（含 CF 挑战页 403）也返回响应体，由调用方按页面特征给出明确报错
            resp = _creq.get(
                url, impersonate="chrome124", timeout=REQUEST_TIMEOUT,
                proxies=_proxies() or None, cookies=cookies,
                allow_redirects=True,
            )
        except ImportError:
            resp = None
        except Exception:
            # 指纹请求失败（如代理协议不兼容）时退回普通 requests 再试一次
            resp = None
    if resp is None:
        resp = _get(url, cookies=cookies)

    if _is_cf_challenge(resp):
        fs = _flaresolverr_get(url, cookies=cookies)
        if fs is not None:
            return fs
    return resp


def _get(url, cookies=None, extra_headers=None):
    last = None
    headers = dict(HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    for _ in range(RETRY):
        try:
            r = _session.get(
                url, headers=headers, timeout=REQUEST_TIMEOUT,
                proxies=_proxies(), allow_redirects=True, cookies=cookies,
            )
            # CF 挑战页（403/503 等）先不抛，交给下面的 FlareSolverr 判断
            if _is_cf_challenge(r):
                fs = _flaresolverr_get(url, cookies=cookies)
                if fs is not None:
                    return fs
            r.raise_for_status()
            return r
        except Exception as exc:  # noqa: BLE001 - 站点异常多样，统一兜底
            last = exc
            # 4xx/5xx 的 HTTPError 里带响应，若正是 CF 挑战页则走 FlareSolverr
            resp = getattr(exc, "response", None)
            if _is_cf_challenge(resp):
                fs = _flaresolverr_get(url, cookies=cookies)
                if fs is not None:
                    return fs
    raise last


def _text(node):
    return node.get_text(" ", strip=True) if node else ""


def _pick(soup, selectors):
    for sel in selectors:
        node = soup.select_one(sel)
        if node:
            return node
    return None


def _extract_date(soup):
    """优先按 label 文本定位日期，再做全文正则兜底。"""
    for label in DATE_LABELS:
        for tag in soup.find_all(string=re.compile(re.escape(label), re.I)):
            parent = tag.parent
            # 在 label 所在的容器里找日期
            for _ in range(3):
                if parent is None:
                    break
                m = DATE_RE.search(parent.get_text(" ", strip=True))
                if m:
                    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
                parent = parent.parent
    for sel in DATE_SELECTORS:
        node = soup.select_one(sel)
        if node:
            m = DATE_RE.search(_text(node))
            if m:
                return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = DATE_RE.search(soup.get_text(" ", strip=True))
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


def _extract_actress(soup):
    names = []
    for sel in ACTRESS_SELECTORS:
        for a in soup.select(sel):
            name = _text(a)
            if not name or len(name) >= 40:
                continue
            # 转成简体中文名（查映射表 + 繁简替换）
            name = to_chinese(name)
            if name and name not in names:
                names.append(name)
        if names:
            break
    if names:
        return names

    # JavDB 详情页兜底：演员在 panel-block 的「演员/演員/出演者」标签后。
    # locale=zh-CN 时是简体「演员」，label 是 <strong> 而非 .meta-label。
    for block in soup.select(".panel-block"):
        label = block.select_one("strong, .meta-label")
        if not label:
            continue
        label_text = label.get_text(strip=True)
        if not any(k in label_text for k in ("演员", "演員", "出演者")):
            continue
        for a in block.select('a[href*="/actors/"]'):
            name = _text(a)
            if not name or len(name) >= 40:
                continue
            name = to_chinese(name)
            if name and name not in names:
                names.append(name)
        if names:
            break
    return names


def _extract_title(soup, code):
    node = _pick(soup, TITLE_SELECTORS)
    if node:
        title = _text(node)
        # 标题常带番号前缀，去掉避免重复
        title = re.sub(r"^\s*" + re.escape(code) + r"\s*", "", title, flags=re.I)
        return title.strip()
    og = soup.select_one('meta[property="og:title"]')
    if og and og.get("content"):
        return og["content"].strip()
    return ""


def _abs_url(url, base):
    """把相对/协议相对 URL 转成绝对 URL。"""
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = base + url
    return url if url.startswith("http") else ""


def _extract_cover_url(soup, base):
    # og:image 单独优先取（很多站点的分享图就是最清晰封面，且不受懒加载影响）
    og = soup.select_one('meta[property="og:image"], meta[name="twitter:image"]')
    if og:
        url = _abs_url(og.get("content", ""), base)
        if url:
            return url

    for sel in COVER_SELECTORS:
        for node in soup.select(sel):
            # 依次尝试常见属性，含各种懒加载写法
            candidates = []
            for attr in ("src", "data-src", "data-original", "data-echo", "href", "content"):
                v = node.get(attr)
                if v:
                    candidates.append(v)
            # srcset 形如 "a.jpg 1x, b.jpg 2x"，取最后一个（通常最大）
            srcset = node.get("srcset") or node.get("data-srcset")
            if srcset:
                parts = [p.strip().split()[0] for p in srcset.split(",") if p.strip()]
                candidates.extend(reversed(parts))
            for raw in candidates:
                url = _abs_url(raw, base)
                if url:
                    return url
    return ""


def _parse_detail(html, code, base):
    soup = BeautifulSoup(html, "lxml")
    return {
        "title": _extract_title(soup, code),
        "actress": _extract_actress(soup),
        "release_date": _extract_date(soup),
        "cover_url": _extract_cover_url(soup, base),
    }


def fetch_javbus(code):
    # 带上站内 Referer，部分 CDN/WAF 会校验来源
    r = _get(f"{JAVBUS_BASE}/{code}", extra_headers={"Referer": JAVBUS_BASE + "/"})
    return _parse_detail(r.text, code, JAVBUS_BASE)


def _javdb_cookies():
    """把配置里的 Cookie 字符串解析成 dict；未配置返回 None。"""
    s = (JAVDB_COOKIES or "").strip()
    if not s:
        return None
    out = {}
    for part in s.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out or None


def _load_javdb_ids():
    """读取番号→详情页ID 缓存（data/javdb_ids.json）。"""
    try:
        with open(JAVDB_IDS_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _remember_javdb_id(code, jid):
    """记住番号对应的 JavDB 详情页 ID，下次直接走详情页绕过搜索墙。"""
    if not code or not jid:
        return
    import tempfile
    # 统一存纯 ID（兼容传入 /v/xxx 或完整链接的情况）
    m = re.search(r"/v/([a-zA-Z0-9]{4,12})", str(jid))
    if m:
        jid = m.group(1)
    data = _load_javdb_ids()
    data[code.upper()] = jid
    try:
        os.makedirs(os.path.dirname(JAVDB_IDS_PATH), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(JAVDB_IDS_PATH), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, JAVDB_IDS_PATH)
    except OSError:
        pass


def fetch_javdb_detail(jid, code=""):
    """直接用 /v/<ID> 抓 JavDB 详情页——详情页无登录墙，机房 IP 也能直连。

    jid 可以是纯 ID（p4aG5）、/v/p4aG5 或完整 URL；code 用于辅助标题提取。
    """
    jid = str(jid or "").strip()
    m = re.search(r"/v/([a-zA-Z0-9]{4,12})", jid)
    if m:
        jid = m.group(1)
    if not re.fullmatch(r"[a-zA-Z0-9]{4,12}", jid):
        raise RuntimeError("JavDB 详情页 ID 无效")
    import time
    url = f"{JAVDB_BASE}/v/{jid}?locale=zh-CN"
    last = None
    for attempt in range(4):
        try:
            r = _get(url, cookies=_javdb_cookies())
            return _parse_detail(r.text, code or jid, JAVDB_BASE)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < 3:
                time.sleep(2 ** attempt)
    raise last


def _search_javdb_id(query_code, code=""):
    """按番号搜 JavDB，返回最匹配条目的详情页 ID；找不到返回 ''。

    匹配策略：优先编号完全一致（忽略空格/横杠/大小写），其次标题含该编号，最后取第一条。
    """
    import time
    cookies = _javdb_cookies()
    search_url = f"{JAVDB_BASE}/search?q={query_code}&f=all&locale=zh-CN"
    # 搜索页偶发 403（高频请求临时封锁），带退避重试；真登录墙才报错
    soup = None
    for attempt in range(4):
        try:
            r = _get(search_url, cookies=cookies)
            soup = BeautifulSoup(r.text, "lxml")
            page_title = soup.select_one("title")
            if page_title and "登入" in page_title.get_text(strip=True):
                raise RuntimeError("JAVDB_LOGIN_WALL")
            break
        except RuntimeError:
            raise RuntimeError(
                "JavDB 要求登录（在部署环境变量 JAVDB_COOKIES 里填入浏览器复制的 JavDB Cookie 后可抓取）"
            )
        except Exception:
            if attempt == 3:
                raise RuntimeError("JavDB 搜索暂时不可用（可能请求过于频繁），请稍后重试")
            time.sleep(2 ** attempt)
    if soup is None:
        return ""
    norm = lambda s: re.sub(r"[\s\-_]", "", str(s or "")).lower()
    target_q = norm(code or query_code)
    items = soup.select(".movie-list .item, .item, .video-item, .result")
    exact = fuzzy = None
    # 顺手缓存本页所有「番号→ID」映射，减少后续搜索次数（JavDB 有频率限制）
    page_map = {}
    code_re = re.compile(r"\b([A-Z]{2,6}-\d{3,6}|FC2(?:PPV)?-?\d{5,8})\b", re.I)
    for item in items:
        a = item.select_one('a[href*="/v/"]')
        if not a or not a.get("href"):
            continue
        href = a["href"]
        text = item.get_text(" ", strip=True)
        title = a.get("title", "")
        m = re.search(r"/v/([a-zA-Z0-9]{4,12})", href)
        # 提取该项里的番号（优先标题，其次文本）
        found = code_re.findall(title) or code_re.findall(text)
        if m and found:
            cid = found[0].upper().replace(" ", "")
            page_map[cid] = m.group(1)
        if exact is None and (target_q and target_q in norm(text) or target_q in norm(title)):
            exact = href
        if fuzzy is None:
            fuzzy = href
    if page_map:
        # 批量并入缓存（一次写盘）
        cache = _load_javdb_ids()
        cache.update(page_map)
        try:
            import tempfile
            os.makedirs(os.path.dirname(JAVDB_IDS_PATH), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(JAVDB_IDS_PATH), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=1)
            os.replace(tmp, JAVDB_IDS_PATH)
        except OSError:
            pass
    return exact or fuzzy or ""


def fetch_javdb(code):
    """JavDB 抓取：先按番号搜索得到详情页 ID（并缓存），再抓详情页取标题/日期/封面/演员。

    带 locale=zh-CN 请求中文版，演员名/标题优先返回中文。
    """
    cookies = _javdb_cookies()
    # 1) 缓存命中：直接抓详情页
    jid = _load_javdb_ids().get(code.upper())
    if not jid:
        # 2) 按番号自动搜索匹配详情页 ID
        jid = _search_javdb_id(code, code)
        if jid:
            _remember_javdb_id(code, jid)
    if jid:
        try:
            return fetch_javdb_detail(jid, code)
        except Exception:
            pass  # 详情页失败再回退到重新搜索
    # 3) 兜底：直接解析搜索页
    r = _get(f"{JAVDB_BASE}/search?q={code}&f=all&locale=zh-CN", cookies=cookies)
    return _parse_detail(r.text, code, JAVDB_BASE)


def _missav_label_value(soup, label):
    """MissAV 详情页按元数据标签（發行日期/番號/標題/女優）取标签后的文本。"""
    for node in soup.find_all(string=re.compile(r"^" + re.escape(label) + r"\s*[:：]")):
        parent = node.parent
        if parent is None:
            continue
        text = parent.get_text(" ", strip=True)
        value = re.sub(r"^" + re.escape(label) + r"\s*[:：]\s*", "", text).strip()
        if value:
            return value
    return ""


def fetch_missav(code):
    """MissAV 详情页（https://missav123.com/<小写番号>）。

    元数据区有 番號/標題/女優/發行日期 标签，比通用选择器可靠；
    h1 常是机翻标题，优先用「標題」字段里的原始日文标题。
    Cloudflare 挑战页以 curl_cffi 浏览器指纹请求，仍被拦时明确报错。
    """
    path = re.sub(r"[^a-z0-9]+", "-", (code or "").lower()).strip("-")
    r = _impersonate_get(f"{MISSAV_BASE}/{path}")
    soup = BeautifulSoup(r.text, "lxml")
    t = soup.select_one("title")
    if t and "just a moment" in t.get_text(strip=True).lower():
        raise RuntimeError(
            "MissAV 有 Cloudflare 人机验证（当前出口 IP 被拦，无法直连抓取）"
        )
    hn = _missav_label_value(soup, "番號")
    if hn and hn.split()[0].upper() != code.strip().upper():
        raise RuntimeError(f"MissAV 返回的番号不符（{hn}）")

    title = _missav_label_value(soup, "標題") or _extract_title(soup, code)
    if title:
        title = re.sub(r"^\s*" + re.escape(code) + r"\s*", "", title, flags=re.I).strip()

    actress = []
    for a in soup.select('a[href*="/actresses/"]'):
        raw = _text(a)
        if not raw or len(raw) >= 40:
            continue
        name = to_chinese(raw.split("(")[0].strip())
        if name and name not in actress:
            actress.append(name)

    return {
        "title": title,
        "actress": actress,
        "release_date": _extract_date(soup),
        "cover_url": _extract_cover_url(soup, MISSAV_BASE),
    }


def fetch_avbase(code):
    """AVBase 详情页：DMM 聚合数据，JavBus/JavDB 拿不到标题/演员时兜底。"""
    from urllib.parse import quote

    r = _get(f"{AVBASE_BASE}/works/{quote(code)}")
    soup = BeautifulSoup(r.text, "lxml")
    data = _parse_detail(r.text, code, AVBASE_BASE)

    # AVBase 的出演者区块很稳定，选不到通用演员选择器时再按 talents 链接兜底
    if not data["actress"]:
        names = []
        for a in soup.select('a[href*="/talents/"]'):
            name = _text(a)
            if not name or len(name) >= 40:
                continue
            name = to_chinese(name)
            if name and name not in names:
                names.append(name)
        data["actress"] = names

    # AVBase 通常聚合了 DMM cid；没有 og:image 时用 DMM 规律图兜底
    if not data["cover_url"]:
        for cid in _dmm_cids(code):
            for pattern in ("digital/videoa", "digital/video", "mono/video"):
                data["cover_url"] = f"https://pics.dmm.co.jp/{pattern}/{cid}/{cid}pl.jpg"
                break
            break
    return data


def _strip_code_suffix(code):
    """去掉 -C/-4K/-REMUX 等版本后缀，用于推断 DMM CID。"""
    c = (code or "").strip().upper()
    for _ in range(4):
        c2 = re.sub(r"-(?:C\d*|CD\d*|4K|2K|1080P|60FPS|REMUX|BLURAY|UHD|U)$", "", c)
        if c2 == c:
            break
        c = c2
    return c


def _dmm_cids(code):
    """按番号推断 DMM CID：DLDSS-584 -> dldss00584 / dldss584，
    部分厂牌的 CID 带 h_086 之类厂家前缀，一并尝试。"""
    c = _strip_code_suffix(code)
    if c.startswith("FC2PPV") or c.startswith("FC2"):
        return []
    m = re.match(r"^([A-Z]+)-?(\d+)$", c)
    if not m:
        return [c.lower()]
    prefix, num = m.groups()
    num = num.lstrip("0") or "0"
    base = [prefix.lower() + num.zfill(5), prefix.lower() + num]
    prefixed = ["h_086" + b for b in base]
    return prefixed + base


def fetch_dmm(code):
    """DMM 直连详情页；地区拦截时快速失败，不影响其他站。"""
    errors = []
    for cid in _dmm_cids(code):
        for service in ("digital/videoa", "mono/video"):
            url = f"{DMM_BASE}/{service}/-/detail/=/cid={cid}/"
            try:
                r = _get(url, cookies={"age_check_done": "1"})
                # DMM 地区封锁会 302 到 not-available-in-your-region，再试也是白费
                if "not-available-in-your-region" in r.url or "not-available-in-your-region" in r.text:
                    raise RuntimeError("地区不可访问（DMM 封锁当前 IP，跳过）")
                data = _parse_detail(r.text, code, DMM_BASE)
                if data.get("title") or data.get("actress") or data.get("release_date") or data.get("cover_url"):
                    return data
                errors.append(f"{cid}: 页面无有效字段")
            except RuntimeError as exc:
                if "地区不可访问" in str(exc):
                    raise
                errors.append(f"{cid}: {exc}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{cid}: {exc}")
    raise RuntimeError(" / ".join(errors) or "DMM 未找到有效数据")


def _is_chinese_name(name):
    """名字里含汉字（CJK 统一表意文字）视为中文名，优先级高于假名/罗马字。"""
    return any("\u4e00" <= ch <= "\u9fff" for ch in (name or ""))


def _merge(results):
    """results: [(site, dict|None, err)] -> 合并后的 dict。

    各字段按站点可靠性取值：标题/演员优先 javdb，发行日期优先 avbase，
    封面优先 javdb/javbus；所有写法都收集到 actress_all 供前端归一。
    """
    merged = {"title": "", "actress": [], "actress_all": [], "release_date": "",
              "cover_url": "", "source": "", "errors": []}
    by_site = {}

    for site, data, err in results:
        if err:
            merged["errors"].append(f"{site}: {err}")
            continue
        if not data:
            continue
        by_site[site] = data
        merged["source"] = (merged["source"] + " " + site).strip()
        # 收集所有演员写法，前端可据此把多个名字归一到一个中文名
        for a in data.get("actress", []):
            cn = to_chinese(a)
            for v in (cn, a):
                if v and v not in merged["actress_all"]:
                    merged["actress_all"].append(v)

    def preferred_names(names):
        chinese = [n for n in names if _is_chinese_name(n)]
        return chinese or names

    for site in ("javdb", "missav", "avbase", "dmm", "javbus"):
        names = [to_chinese(a) for a in by_site.get(site, {}).get("actress", [])]
        if names:
            merged["actress"] = preferred_names(names)
            break

    def first(key, priorities):
        for site in priorities:
            value = by_site.get(site, {}).get(key)
            if value:
                return value
        return ""

    merged["title"] = first("title", ("javdb", "missav", "avbase", "dmm", "javbus"))
    merged["release_date"] = first("release_date", ("avbase", "javdb", "missav", "dmm", "javbus"))
    merged["cover_url"] = first("cover_url", ("javdb", "missav", "javbus", "avbase", "dmm"))
    return merged


def _make_thumb(filename, max_size=(360, 240)):
    """为卡片列表生成小图；详情页仍用原图。Pillow 缺失或损坏图片时静默跳过。"""
    try:
        from PIL import Image
        src = os.path.join(COVER_DIR, filename)
        thumb_dir = os.path.join(COVER_DIR, "thumbs")
        os.makedirs(thumb_dir, exist_ok=True)
        thumb_name = os.path.splitext(filename)[0] + ".jpg"
        with Image.open(src) as img:
            img = img.convert("RGB")
            img.thumbnail(max_size, Image.LANCZOS)
            img.save(os.path.join(thumb_dir, thumb_name), "JPEG", quality=75, optimize=True)
    except Exception:
        pass


def download_cover(url, code):
    """下载封面到本地，返回 (文件名, 错误信息)。

    站点图片普遍有防盗链，必须带对应来源站的 Referer 才能下载，
    因此按 URL 域名推断 Referer，两个站都试一遍以确保成功。
    """
    if not url:
        return "", "未抓到封面地址"

    # 按图片 URL 域名推断可能的 Referer（防盗链来源）
    referers = []
    if "javdb" in url:
        referers.append(JAVDB_BASE + "/")
    if "javbus" in url:
        referers.append(JAVBUS_BASE + "/")
    if "fourhoi" in url or "missav" in url:
        referers.append(MISSAV_BASE + "/")
    if "pics.dmm" in url or "dmm.co" in url or "avbase" in url:
        referers.append(DMM_BASE + "/")
    # 兜底：几个站都试
    for ref in (JAVBUS_BASE + "/", MISSAV_BASE + "/", JAVDB_BASE + "/"):
        if ref not in referers:
            referers.append(ref)

    last_err = "未知错误"
    for referer in referers:
        headers = dict(HEADERS)
        headers["Referer"] = referer
        try:
            r = _session.get(
                url, headers=headers, timeout=REQUEST_TIMEOUT,
                proxies=_proxies(), stream=True,
            )
            r.raise_for_status()
            ctype = r.headers.get("Content-Type", "")
            # 有些站点对失败请求返回 HTML，按 Content-Type 排除
            if "text/html" in ctype:
                last_err = "返回的是 HTML（防盗链拦截）"
                continue
            ext = ".webp" if "webp" in ctype else ".png" if "png" in ctype else ".jpg"
            filename = re.sub(r"[^A-Za-z0-9_-]", "_", code) + ext
            path = os.path.join(COVER_DIR, filename)
            with open(path, "wb") as f:
                for chunk in r.iter_content(8192):
                    if chunk:
                        f.write(chunk)
            if os.path.getsize(path) > 1024:  # 至少 1KB，过滤错误占位图
                _make_thumb(filename)
                return filename, ""
            last_err = "下载文件过小"
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
    return "", last_err


def scrape(code):
    """对外主入口：并发抓两站，合并字段并下载封面。永不抛异常。"""
    code = (code or "").strip().upper()
    if not code:
        return {"code": "", "title": "", "actress": [], "actress_all": [], "release_date": "",
                "cover_path": "", "source": "", "errors": ["编号不能为空"]}

    def run(site, fn):
        try:
            return site, fn(code), None
        except Exception as exc:  # noqa: BLE001
            return site, None, str(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        f1 = ex.submit(run, "javbus", fetch_javbus)
        f2 = ex.submit(run, "javdb", fetch_javdb)
        f3 = ex.submit(run, "avbase", fetch_avbase)
        f4 = ex.submit(run, "dmm", fetch_dmm)
        f5 = ex.submit(run, "missav", fetch_missav)
        results = [f1.result(), f2.result(), f3.result(), f4.result(), f5.result()]

    merged = _merge(results)
    cover_path, cover_err = download_cover(merged["cover_url"], code)
    if cover_err:
        merged["errors"].append(f"封面: {cover_err}")
    return {
        "code": code,
        "title": merged["title"],
        "actress": merged["actress"],
        "actress_all": merged["actress_all"],
        "release_date": merged["release_date"],
        "cover_path": cover_path,
        "cover_url": merged["cover_url"],
        "source": merged["source"],
        "errors": merged["errors"],
    }
