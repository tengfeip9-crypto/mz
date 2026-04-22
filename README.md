# MZ

这个仓库现在分成两类入口：

- 普通用户：直接使用 [portable_app/README.md](portable_app/README.md) 对应的便携目录
- 开发者：继续使用源码、打包脚本和现有模块结构

## 普通用户怎么用

如果你的目标只是“下载后直接运行”，不要先研究源码，直接看 `portable_app/`：

```text
portable_app/
├─ 1_start_qzone_chrome.bat
├─ 2_open_mz_control_panel.bat
├─ MZ_Control_Panel.exe
├─ .env.example
└─ README.md
```

推荐使用顺序：

1. 下载整个仓库 ZIP 并解压
2. 进入 `portable_app/`
3. 按 `README.md` 完成首次配置
4. 先运行 `1_start_qzone_chrome.bat`
5. 再运行 `2_open_mz_control_panel.bat`

## 开发者目录

如果你是维护这个项目的人，核心源码仍然在这些位置：

```text
.
├─ mz.py                       # 自动点赞主入口
├─ mz_control_panel.py         # 总控面板源码
├─ project_config.py           # 全局配置与路径解析
├─ mz_core/                    # 自动化核心逻辑
├─ remote_login/               # 远程登录 Web 服务
├─ build_mz_control_panel.ps1  # 构建单文件 exe
├─ publish_portable_app.ps1    # 同步便携目录内的 exe
└─ portable_app/               # 面向普通用户的便携包目录
```

## 从源码构建

1. 创建虚拟环境并安装依赖：

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

2. 构建主程序：

   ```powershell
   .\build_mz_control_panel.ps1
   ```

3. 把新构建的 exe 同步到便携目录：

   ```powershell
   .\publish_portable_app.ps1
   ```

## 配置说明

- `MZ_QQ_NUMBER`：当前账号 QQ 号，好友抓取与说说发布都会用到
- `MZ_DEBUGGER_ADDRESS`：Chrome 远程调试地址，默认 `127.0.0.1:9222`
- `MZ_CHROMEDRIVER_PATH`：ChromeDriver 路径；便携版通常可以留空，直接使用内置驱动
- `MZ_CHROME_PATH`：Chrome 可执行文件路径
- `MZ_CHROME_USER_DATA_DIR`：远程调试模式使用的 Chrome 用户数据目录
- `MZ_FRIEND_DATA_DIR`：好友快照与导出文件目录
- `MZ_RUN_LOG_DIR`：运行日志与网页登录会话目录
- `MZ_REMOTE_LOGIN_DATA_DIR`：网页登录用户数据目录
