# 影片库 JavLib（1Panel 本地应用）

按编号从 JavBus / JavDB 抓取影片信息与封面，本地归档；下载地址、录入时间等由自己补录；可按**发行时间 / 录入时间**（可切换）或演员名检索。带登录密码保护。

## 功能

- 输入编号（如 `ABP-123`），自动抓取标题、演员、发行时间、封面，两站结果互补合并
- 封面下载到本地，避免外链失效
- 手动补录：下载地址、录入时间、分辨率、是否原档、是否 REMUX、帧率、备注
- 卡片网格展示，属性以彩色标签显示（分辨率/原档/REMUX/帧率）
- 每张卡片一个「复制」按钮：点击即复制下载地址（下载与复制合并为一个按钮）
- 检索：发行时间 / 录入时间双模式切换 + 日期区间 + 演员 + 编号/标题关键字
- 登录密码保护

---

## 一、在 1Panel 中安装（推荐）

本项目是符合 1Panel 规范的**本地应用包**。安装步骤如下：

1. 把本目录（`javlib/`）整个上传到服务器的 1Panel 本地应用目录：

   ```bash
   # 1Panel 本地应用默认目录（若你的版本不同，以实际路径为准）
   /opt/1panel/resource/apps/local/
   ```

   上传后应形成：`/opt/1panel/resource/apps/local/javlib/`，其下包含 `data.yml`、`logo.png`、`README.md`、`1.0.0/`。

2. 登录 1Panel → 左侧「应用商店」→ 右上角切换到「**本地应用**」标签。

3. 找到「**影片库**」，点「安装」。安装表单可填：
   - **HTTP 端口**：对外访问端口，默认 `5000`
   - **登录密码**：网页登录密码（默认 `admin`，建议修改）
   - **会话密钥**：保持默认或改成一段随机字符串
   - **HTTP 代理**：目标服务器能直连两个站点时**留空**；不能直连时填 `http://代理地址:端口`

4. 等待构建完成（首次会从国内镜像源装依赖，约 1-3 分钟）。

5. 安装后在「容器」中看到 `javlib-xxxx` 容器即成功。浏览器访问：

   ```
   http://服务器IP:端口
   ```

   输入安装时设置的密码进入。

> 数据持久化：数据库和封面都在版本目录的 `data/` 下，并已映射到容器 `/app/data`。**升级/重装前请备份 `data/` 目录**。

---

## 二、本地直接运行（无 Docker，开发调试用）

在 `1.0.0/` 目录下：

```bash
# 双击 start_local.bat（Windows），或命令行：
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
python app.py
```

浏览器打开 `http://localhost:5000`，默认密码 `admin`。

---

## 三、目录结构

```
javlib/                      # 1Panel 本地应用根目录
├── data.yml                 # 应用声明（名称/标签/类型）
├── logo.png                 # 应用图标 180x180
├── README.md
└── 1.0.0/                   # 版本目录
    ├── data.yml             # 安装表单配置（端口/密码/密钥/代理）
    ├── docker-compose.yml   # 1Panel 编排（含 1panel-network、卷映射）
    ├── Dockerfile
    ├── requirements.txt
    ├── app.py               # Flask 服务（页面 + API + 认证）
    ├── config.py            # 集中配置（全部可用环境变量覆盖）
    ├── db.py                # SQLite 数据层
    ├── scraper.py           # 双站抓取解析器（选择器集中在顶部）
    ├── static/              # 前端样式与脚本
    ├── templates/           # 登录页与主页
    └── data/                # 持久化数据（db + 封面）
```

## 四、常见问题

- **抓取失败 / 字段为空**：站点可能改版或需要代理。先在表单填代理试；若某字段长期抓不到，可在录入弹窗里手动补填——抓取失败不阻断入库。
- **改版后抓不到**：编辑 `scraper.py` 顶部的 `TITLE_SELECTORS / ACTRESS_SELECTORS / DATE_LABELS / COVER_SELECTORS` 即可，无需改其他代码。
- **忘记密码**：进入容器执行 `export APP_PASSWORD=新密码` 后重启，或用 1Panel 重新安装并设置新密码。
- **端口访问不到**：检查 1Panel 防火墙/安全组是否放行该端口。

## 五、说明

本项目仅用于个人影片信息管理，抓取公开元数据。请遵守当地法律法规，勿用于任何违规用途。
