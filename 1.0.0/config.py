"""集中配置：全部可通过环境变量覆盖，方便本地运行与容器内运行共用一套代码。"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
COVER_DIR = os.path.join(DATA_DIR, "covers")
DB_PATH = os.path.join(DATA_DIR, "movies.db")

# 登录密码：默认 admin，部署时务必通过环境变量修改
APP_PASSWORD = os.environ.get("APP_PASSWORD", "admin")
# Flask session 签名密钥：生产环境请改成一段随机长字符串
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-to-a-random-string-before-deploy")

# 服务端口
PORT = int(os.environ.get("PORT", "5000"))

# 抓取超时（秒）与并发两站时的重试次数
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "15"))
RETRY = int(os.environ.get("RETRY", "2"))
# 浏览器指纹请求（curl_cffi）单独的超时：JavDB 经代理/跨区链路单次可达 20s+，
# 用 REQUEST_TIMEOUT(15s) 会掐死本可成功的请求，随后回退普通请求再白等 30s。
SCRAPE_TIMEOUT = int(os.environ.get("SCRAPE_TIMEOUT", "30"))

# 可选代理：配置项写在这里即可（如 http://127.0.0.1:7897），留空表示直连。
# 目标机器可直连两个站点，保持 "" 即可。
HTTP_PROXY = os.environ.get("HTTP_PROXY", "")

# JavDB 登录 Cookie（可选）：JavDB 对搜索加了登录墙，把浏览器里复制的 Cookie
# 整串贴进来（如 "remember_id=xx; remember_token=yy; ..."）即可免登录抓取。
JAVDB_COOKIES = os.environ.get("JAVDB_COOKIES", "")

# 番号 → JavDB 详情页 ID 缓存：搜索页有登录墙，但详情页可直连。
# 成功刮过的番号记下 ID 后，下次直接走详情页，绕开搜索墙。存在挂载卷内，升级不丢。
JAVDB_IDS_PATH = os.path.join(DATA_DIR, "javdb_ids.json")

# 启用的刮削源（逗号分隔，可选 javdb/airav/carib/javbus/avbase/dmm/missav）。
# 默认 javdb,airav,carib：凤凰城出口实测（原生网络，无过盾）——JavDB 需指纹，
# airav.io/cn（简体标题/封面）与 caribbeancom（无码，日文标题/演员）原生可达，
# 作为兜底；其余源被 CF 拦/地区锁/无接口，默认关闭避免白等。
SCRAPER_SOURCES = os.environ.get("SCRAPER_SOURCES", "javdb,airav,carib")

os.makedirs(COVER_DIR, exist_ok=True)
