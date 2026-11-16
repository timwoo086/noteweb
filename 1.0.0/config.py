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

os.makedirs(COVER_DIR, exist_ok=True)
