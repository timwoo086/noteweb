"""演员名归一：基于 avdb 的 6 万条别名映射表，把同一演员的多种写法统一为中文名。

映射表来源：leolitaly/avdb 项目的 actor-mapping.xml，转换成紧凑的
actress_mapping.json（别名小写 -> 规范中文名）。用户自己在「演员别名」
里设置的分组优先级最高，可覆盖自动归一结果。
"""
import json
import os

_MAP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "actress_mapping.json")

# 繁体/日文常见用字 -> 简体，映射表未命中时的兜底
_SIMPLE = {
    "結": "结", "亞": "亚", "亜": "亚", "橋": "桥", "綺": "绮", "羅": "罗",
    "彌": "弥", "沢": "泽", "澤": "泽", "沖": "冲", "愛": "爱", "恵": "惠",
    "詩": "诗", "織": "织", "戸": "户", "関": "关", "涼": "凉", "飯": "饭",
    "岡": "冈", "姫": "姬", "麗": "丽", "華": "华", "優": "优", "緒": "绪",
    "穂": "穗", "鈴": "铃", "楓": "枫", "awai": "awai",
}

_alias_to_cn = None
_cn_to_aliases = None


def _load():
    global _alias_to_cn, _cn_to_aliases
    if _alias_to_cn is not None:
        return
    try:
        with open(_MAP_PATH, encoding="utf-8") as f:
            _alias_to_cn = json.load(f)
    except (OSError, ValueError):
        _alias_to_cn = {}
    _cn_to_aliases = {}
    for alias, cn in _alias_to_cn.items():
        _cn_to_aliases.setdefault(cn, []).append(alias)


def normalize(name):
    return (name or "").strip().lower()


def is_chinese(name):
    """含 CJK 汉字即视为中文名。"""
    return any("\u4e00" <= ch <= "\u9fff" for ch in (name or ""))


def to_cn(name):
    """把演员名转成规范中文名：先查 avdb 映射表，再做繁简替换兜底。"""
    name = (name or "").strip()
    if not name:
        return name
    _load()
    hit = _alias_to_cn.get(normalize(name))
    if hit:
        return hit
    return "".join(_SIMPLE.get(ch, ch) for ch in name)


def candidates(name, aliases=None):
    """返回同一演员的所有写法，中文名排第一。

    aliases 为用户在「演员别名」里设置的分组，优先级最高：
    若名字属于某个用户分组，直接返回该组全部名字。
    """
    name = (name or "").strip()
    _load()

    # 用户自定义分组优先
    for g in aliases or []:
        members = [g.get("name", "")] + list(g.get("aliases", []))
        members = [m for m in members if m]
        if name in members or to_cn(name) in members:
            result = [members[0]] if members else []
            for m in members:
                for v in (m, to_cn(m)):
                    if v and v not in result:
                        result.append(v)
            return result

    cn = to_cn(name)
    pool = [cn] if cn else []
    for alias in _cn_to_aliases.get(cn, []):
        # 反查表里的键是小写，尽量还原原始写法首字母大写显示
        display = alias
        if alias.isascii() and alias:
            display = alias[0].upper() + alias[1:]
        if display and display not in pool:
            pool.append(display)
    if name and name not in pool:
        pool.append(name)
    return pool
