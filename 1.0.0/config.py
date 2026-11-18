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

# 可选代理：配置项写在这里即可（如 http://127.0.0.1:7897），留空表示直连。
# 目标机器可直连两个站点，保持 "" 即可。
HTTP_PROXY = os.environ.get("HTTP_PROXY", "")

# JavDB 登录 Cookie（可选）：JavDB 对搜索加了登录墙，把浏览器里复制的 Cookie
# 整串贴进来（如 "remember_id=xx; remember_token=yy; ..."）即可免登录抓取。
JAVDB_COOKIES = os.environ.get("JAVDB_COOKIES", "")

# 番号 → JavDB 详情页 ID 缓存：搜索页有登录墙，但详情页可直连。
# 成功刮过的番号记下 ID 后，下次直接走详情页，绕开搜索墙。存在挂载卷内，升级不丢。
JAVDB_IDS_PATH = os.path.join(DATA_DIR, "javdb_ids.json")

# FlareSolverr（可选）：用于绕过 Cloudflare 人机验证 / JS Challenge。
# 留空表示不启用；填入 FlareSolverr 服务地址（如 http://flaresolverr:8191 或
# http://192.168.5.3:8191）后，正常抓取被 CF 挑战页拦截时会自动改走它求解，
# 拿回真实页面与 cf_clearance cookie。对 MissAV / JavBus 等 CF 站有用。
FLARESOLVERR_URL = os.environ.get("FLARESOLVERR_URL", "").rstrip("/")
# FlareSolverr 求解较耗时，默认超时与缓存时长（秒）
FLARESOLVERR_TIMEOUT = int(os.environ.get("FLARESOLVERR_TIMEOUT", "60"))
FLARESOLVERR_CACHE_TTL = int(os.environ.get("FLARESOLVERR_CACHE_TTL", "1800"))

os.makedirs(COVER_DIR, exist_ok=True)
