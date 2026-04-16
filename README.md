# MZ

一个面向 QQ 空间场景的自动化项目，当前包含两部分能力：

- `mz_core/`：自动点赞、好友快照保存、好友变化对比、自动说说发布。
- `remote_login/`：提供网页登录与远程浏览器操作入口，便于在别的设备上辅助登录。

## 目录结构

```text
.
├─ mz.py                       # 自动点赞主入口
├─ launch_chrome_debug.py      # 启动远程网页登录入口
├─ launch_qzone_web.py         # 远程网页登录入口别名
├─ project_config.py           # 全局配置与路径解析
├─ mz_core/                    # 自动化核心逻辑
├─ remote_login/               # 远程登录 Web 服务
├─ requirements.txt            # Python 依赖
└─ .env.example                # 本地配置示例
```

运行时数据默认写入 `.local/`。如果项目根目录里已经存在旧版 `friend_data/`、`run_logs/`、`remote_login_data/`，程序会继续沿用旧目录，避免打断你当前的数据。

## 快速开始

1. 创建虚拟环境并安装依赖：

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

2. 复制 `.env.example` 为 `.env`，至少填写 `MZ_QQ_NUMBER`。

3. 准备 ChromeDriver：

   - 默认读取 `driver/chromedriver.exe`
   - 或者直接在 `.env` 里设置 `MZ_CHROMEDRIVER_PATH`

4. 启动方式：

   ```powershell
   python mz.py
   ```

   ```powershell
   python launch_chrome_debug.py
   ```

## 配置说明

- `MZ_QQ_NUMBER`：当前账号 QQ 号，好友抓取与说说发布都会用到。
- `MZ_DEBUGGER_ADDRESS`：Chrome 远程调试地址，默认 `127.0.0.1:9222`。
- `MZ_CHROMEDRIVER_PATH`：ChromeDriver 路径。
- `MZ_CHROME_PATH`：Chrome 可执行文件路径。
- `MZ_CHROME_USER_DATA_DIR`：远程调试模式使用的 Chrome 用户数据目录。
- `MZ_FRIEND_DATA_DIR`：好友快照与导出文件目录。
- `MZ_RUN_LOG_DIR`：运行日志与网页登录会话目录。
- `MZ_REMOTE_LOGIN_DATA_DIR`：网页登录用户数据目录。

## 上传到 GitHub 前

以下内容已经在 `.gitignore` 中排除，不建议提交：

- 本地 IDE 配置：`.idea/`
- 运行产物：`.local/`、`friend_data/`、`run_logs/`、`remote_login_data/`
- 本地二进制：`driver/`、`cloudflared.exe`
- 本地配置：`.env`

如果你准备公开仓库，建议先确认旧的 `friend_data/` 中没有需要额外清理的历史导出数据。
