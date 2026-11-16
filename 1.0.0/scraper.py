"""双站抓取解析器。

JavBus: 编号直达详情页 https://www.javbus.com/<CODE>
JavDB : 先搜索 https://javdb.com/search?q=<CODE>&f=all 命中后进入详情页

设计要点：
1. 选择器集中放在顶部常量列表，站点改版时只改这一处；每个字段配多个候选选择器容错。
2. 两站并行抓取，结果合并：以先成功者为主，缺失字段用另一站补齐。
3. 封面下载到本地 data/covers，避免防盗链导致外链失效。
4. 任何站点失败都不阻断整体流程，返回已抓到的字段 + 错误说明，用户仍可手动补全。
"""
import concurrent.futures
import os
import re

import requests
from bs4 import BeautifulSoup

from config import COVER_DIR, HTTP_PROXY, REQUEST_TIMEOUT, RETRY

JAVBUS_BASE = "https://www.javbus.com"
JAVDB_BASE = "https://javdb.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,ja;q=0.8,en;q=0.7",
}

# 演员名中文映射表：日文/繁体写法 -> 大陆通用简体中文名。
# 抓取到的演员名会逐个查表转换成中文。可根据需要继续补充。
# （左侧也可写繁体/日文假名写法，命中即替换为右侧中文名）
ACTRESS_CN_MAP = {
    "波多野結衣": "波多野结衣", "波多野衣結": "波多野结衣",
    "三上悠亜": "三上悠亚", "三上悠亚": "三上悠亚",
    "新ありな": "新有菜", "橋本有菜": "桥本有菜", "橋本ありな": "桥本有菜",
    "明日花キララ": "明日花绮罗", "明日花綺羅": "明日花绮罗",
    "河北彩花": "河北彩花", "河北彩伽": "河北彩花",
    "弥生みづき": "弥生美月", "彌生美月": "弥生美月", "弥生美月": "弥生美月",
    "逢坂真帆": "逢坂真帆", "星野ナミ": "星野奈美", "星野なみ": "星野奈美",
    "神咲詩織": "神咲诗织", "神咲しおり": "神咲诗织",
    "椎名そら": "椎名空", "椎名そら": "椎名空",
    "白石茉莉奈": "白石茉莉奈", "白石まわり": "白石茉莉奈",
    "沖田杏梨": "冲田杏梨", "冲田杏梨": "冲田杏梨",
    "有成璃": "有成璃", "佐藤えり": "佐藤绘里",
    "桐谷直葉": "桐谷直叶", "桐谷なお": "桐谷直叶",
    "藍沢夏癒": "蓝泽夏愈", "篠崎愛": "筱崎爱",
    "安室奈美恵": "安室奈美惠",
    "吉沢明歩": "吉泽明步", "吉川蓮": "吉川莲",
    "森下交": "森下交", "愛田奈々": "爱田奈奈",
    "相沢みなみ": "相泽南", "相泽みなみ": "相泽南",
    "岬ななみ": "岬奈奈美", "岬ななみ": "岬奈奈美",
    "戸田真琴": "户田真琴", "户田真琴": "户田真琴",
    "涼森れむ": "凉森梦", "涼森れむ": "凉森梦",
    "関根和美": "关根和美", "關根和美": "关根和美",
}


def to_chinese(name):
    """把演员名转成简体中文：先查映射表，再把常见日文/繁体字替换成简体。"""
    if not name:
        return name
    name = name.strip()
    if name in ACTRESS_CN_MAP:
        return ACTRESS_CN_MAP[name]
    # 繁体 -> 简体兜底转换（覆盖常见女优名用字）
    simple_map = {
        "結": "结", "亞": "亚", "亜": "亚", "橋": "桥",
        "綺": "绮", "羅": "罗", "彌": "弥", "沢": "泽", "澤": "泽", "沖": "冲",
        "愛": "爱", "恵": "惠", "詩": "诗", "織": "织", "戸": "户", "関": "关",
        "涼": "凉", "飯": "饭", "岡": "冈", "姫": "姬", "麗": "丽", "華": "华",
        "優": "优", "緒": "绪", "穂": "穗", "鈴": "铃",
    }
    converted = "".join(simple_map.get(ch, ch) for ch in name)
    return converted

# ---------- 选择器表（改版时主要维护这里）----------
# 标题
TITLE_SELECTORS = ["h3", "div.info h3", ".container h3", "h1", ".video-info h2", "strong.title"]
# 演员链接
ACTRESS_SELECTORS = [
    '.info a[href*="/star/"]',
    '.info a[href*="/actor/"]',
    'a[href*="/star/"]',
    '.video-info a[href*="/actors/"]',
    'a[href*="/actor/"]',
]
# 发行日期：JavBus 常见 "发行日期：" 后跟 <a>；JavDB 常见 "日期："
DATE_LABELS = ["发行日期", "発売日", "日期", "上映日期", "release date", "配信開始日"]
DATE_SELECTORS = ['.info a[href*="/date/"]', '.info span.genre + a', '.video-info .value']
# 封面（顺序即优先级，按站点改版时维护这里）
COVER_SELECTORS = [
    'a.bigImage img',                       # JavBus 大图链接
    '.video-cover img',                     # JavDB 详情封面
    '#cover img',
    '.cover img',
    '.cover-wrap img',
    'img[src*="/pics/"]',                   # JavBus 图片 CDN
    'img[src*="cover"]',
    'meta[property="og:image"]',            # 兜底：社交分享图，通常是高清封面
    'meta[name="twitter:image"]',
]

DATE_RE = re.compile(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})")


def _proxies():
    """显式返回代理 dict；空串时用空 dict 禁用系统环境里的代理，保证真正直连。"""
    if HTTP_PROXY:
        return {"http": HTTP_PROXY, "https": HTTP_PROXY}
    return {"http": None, "https": None}


def _get(url):
    last = None
    for _ in range(RETRY):
        try:
            r = requests.get(
                url, headers=HEADERS, timeout=REQUEST_TIMEOUT,
                proxies=_proxies(), allow_redirects=True,
            )
            r.raise_for_status()
            return r
        except Exception as exc:  # noqa: BLE001 - 站点异常多样，统一兜底
            last = exc
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
    url = f"{JAVBUS_BASE}/{code}"
    r = _get(url)
    return _parse_detail(r.text, code, JAVBUS_BASE)


def fetch_javdb(code):
    """JavDB 详情地址是站内 ID，先搜索定位第一命中项。

    带 locale=zh-CN 请求中文版，演员名/标题优先返回中文（avdb 项目的做法）。
    """
    search_url = f"{JAVDB_BASE}/search?q={code}&f=all&locale=zh-CN"
    r = _get(search_url)
    soup = BeautifulSoup(r.text, "lxml")
    # 优先匹配列表里编号完全一致项，否则取第一个结果
    target = None
    for item in soup.select(".item, .video-item, .result"):
        text = item.get_text(" ", strip=True)
        if code.lower() in text.lower():
            a = item.select_one('a[href*="/v/"]')
            if a and a.get("href"):
                target = a["href"]
                break
    if not target:
        a = soup.select_one('a[href*="/v/"]')
        if a and a.get("href"):
            target = a["href"]
    if not target:
        # 有些页面本身就是详情，直接解析搜索页
        return _parse_detail(r.text, code, JAVDB_BASE)
    if target.startswith("/"):
        target = JAVDB_BASE + target
    # 详情页也请求中文版（保留已有 query 参数）
    target = target + ("&" if "?" in target else "?") + "locale=zh-CN"
    detail = _get(target)
    return _parse_detail(detail.text, code, JAVDB_BASE)


def _merge(results):
    """results: [(site, dict|None, err)] -> 合并后的 dict。"""
    merged = {"title": "", "actress": [], "release_date": "", "cover_url": "", "source": "", "errors": []}
    for site, data, err in results:
        if err:
            merged["errors"].append(f"{site}: {err}")
            continue
        if not data:
            continue
        if not merged["title"] and data.get("title"):
            merged["title"] = data["title"]
        if not merged["actress"] and data.get("actress"):
            merged["actress"] = data["actress"]
        if not merged["release_date"] and data.get("release_date"):
            merged["release_date"] = data["release_date"]
        if not merged["cover_url"] and data.get("cover_url"):
            merged["cover_url"] = data["cover_url"]
        merged["source"] = (merged["source"] + " " + site).strip()
    return merged


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
    if "javbus" in url or "pics.dmm" in url or "dmm.co" in url:
        referers.append(JAVBUS_BASE + "/")
    # 兜底：两个站都试
    for ref in (JAVBUS_BASE + "/", JAVDB_BASE + "/"):
        if ref not in referers:
            referers.append(ref)

    last_err = "未知错误"
    for referer in referers:
        headers = dict(HEADERS)
        headers["Referer"] = referer
        try:
            r = requests.get(
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
                return filename, ""
            last_err = "下载文件过小"
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
    return "", last_err


def scrape(code):
    """对外主入口：并发抓两站，合并字段并下载封面。永不抛异常。"""
    code = (code or "").strip().upper()
    if not code:
        return {"code": "", "title": "", "actress": [], "release_date": "",
                "cover_path": "", "source": "", "errors": ["编号不能为空"]}

    def run(site, fn):
        try:
            return site, fn(code), None
        except Exception as exc:  # noqa: BLE001
            return site, None, str(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(run, "javbus", fetch_javbus)
        f2 = ex.submit(run, "javdb", fetch_javdb)
        results = [f1.result(), f2.result()]

    merged = _merge(results)
    cover_path, cover_err = download_cover(merged["cover_url"], code)
    if cover_err:
        merged["errors"].append(f"封面: {cover_err}")
    return {
        "code": code,
        "title": merged["title"],
        "actress": merged["actress"],
        "release_date": merged["release_date"],
        "cover_path": cover_path,
        "cover_url": merged["cover_url"],
        "source": merged["source"],
        "errors": merged["errors"],
    }
