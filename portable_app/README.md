# Portable App

这是给普通用户直接使用的便携目录。

如果你是从 GitHub 下载整个仓库的 ZIP，请解压后直接进入这个目录使用，不需要先安装 Python，也不需要理解源码结构。

## 目录说明

- `1_start_qzone_chrome.bat`
  作用：启动带远程调试参数的 Chrome，并打开 QQ 空间页面。
- `2_open_mz_control_panel.bat`
  作用：打开总控面板。
- `MZ_Control_Panel.exe`
  作用：主程序，已经打包为单文件。
- `.env.example`
  作用：首次配置模板。

## 第一次使用

1. 复制 `.env.example` 为 `.env`
2. 打开 `.env`，至少把 `MZ_QQ_NUMBER` 改成你自己的 QQ 号
3. 双击 `1_start_qzone_chrome.bat`
4. 等 Chrome 打开并进入 QQ 空间后，再双击 `2_open_mz_control_panel.bat`
5. 在总控面板里按需设置：
   - 自动说说触发轮数
   - 好友保存触发轮数
   - 好友对比触发轮数
   - 每大轮 sleep 秒数
   - 自动说说内容
   - 自动说说配图
6. 点击“保存配置”或“开始运行”

## 运行数据会写到哪里

程序默认会把运行数据写在当前目录内，不会依赖原仓库路径：

- `.env`
- `mz_user_settings.json`
- `.local/`

如果你把整个 `portable_app/` 文件夹移动到别的位置，程序仍然可以继续运行。

## 常见问题

### 1. 点开面板后提示无法连接浏览器

通常是因为还没有先运行 `1_start_qzone_chrome.bat`，或者 Chrome 没有正常启动。

### 2. Chrome 没有打开

默认脚本会优先尝试这些路径：

- `%ProgramFiles%\Google\Chrome\Application\chrome.exe`
- `%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe`

如果你的 Chrome 装在别的位置，请手动编辑 `.env` 或批处理文件。

### 3. 配图没有生效

请在总控面板的“自动说说配图”区域使用“添加图片”按钮，成功后图片完整路径会显示在列表框中。

## 给维护者

如果你更新了根目录的 `mz_control_panel.exe`，可以运行仓库根目录下的 `publish_portable_app.ps1`，自动同步到这个目录。
