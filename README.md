# Gemini Codex Web

在 Windows 上使用 Codex 桌面或 CLI，通过本机网关将请求交给 Ego Lite 中已登录的 Gemini 网页。项目使用你自己的 Gemini 网页会话和额度。可用模型、响应速度及地区支持由 Gemini 网页决定。

## 安装

1. 安装 Windows 版 Codex 和带 Tkinter 的 Python 3.12 或更新版本。
2. 解压发布包到准备长期使用的目录，打开包内的文件夹，双击 **启动 Gemini Codex.cmd**。第一次双击会安装依赖、创建桌面快捷方式，然后打开应用。缺少 Ego Lite 时，会从官网下载安装器，核验 Citro Labs 的数字签名并打开安装向导。根据 Windows 和 Ego Lite 的提示完成安装与首次设置。如果只想安装，也可双击 `Install.cmd` 或从项目根目录运行下面的命令。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Install.ps1
```

3. 首次启动时，点击「在 Ego Lite 登录」，在 Google 的网页输入自己的账号并完成验证。回到向导，点击「检查并继续」。如果已在 Ego Lite 登录，直接点击「检查并继续」。之后可从解压目录或桌面上的 **Gemini Codex Web** 快捷方式启动。
4. 使用 CLI 时运行 `.\Gemini-Codex.cmd`，并确保 `codex` 在 PATH 中。更换账号或登录失效后，可运行 `.\Gemini-Codex.cmd --setup` 再次检查。

Gemini 的登录状态保存在 Ego Lite，应用只检验网页中是否已登录。浏览器优先使用「用户配置 1」（`Profile 1`），没有该配置时使用 Ego Lite 默认配置。

本地运行数据位于 `%LOCALAPPDATA%\CodexGeminiWeb`。首次启动会生成本地网关凭据和 Codex 配置。已有安装会继续使用该目录里的设置。运行 `.\Gemini-Desktop.cmd` 可以在终端中查看启动错误。

安装版本见 `VERSION`。登录检查结果记录在 `%LOCALAPPDATA%\CodexGeminiWeb\gemini-login.log`。安装脚本优先查找 Python Launcher，也可手动指定 `-Python` 和 Python 可执行文件的完整路径。

## 运行方式

- 网关只监听 `127.0.0.1:18473`。Codex 发送请求前会读取本机生成的网关凭据。
- Gemini 对话使用独立的 Ego 空间。关闭专用 Codex 窗口会停止网关并关闭对应空间，保存的 Gemini 对话地址用于下次续接。
- 模型菜单提供 Gemini 4 Hydrogen 和 Gemini 4 Neon。网关会核对网页实际选中的模型。如果当前账号无法使用所选模型，网关会提示并停止发送。
- Codex 按本机的权限配置执行工具。为回答任务而交给模型的提示词或工具结果会通过当前 Gemini 网页发送。
- 发布包包含源码、安装脚本和测试。Ego Lite 安装器由官网提供，Codex 和 Python 由用户预先安装。

## 本地验证

以下检查无需启动 Gemini 网页。JavaScript 测试需要 Node.js。

```powershell
.\.artifact-venv\Scripts\python.exe -m unittest test_first_run test_lifecycle test_watchdog test_bridge_steps test_resume
node --test test_first_run.mjs test_structured_reply.mjs
```

仓库中另有真人会话集成测试。这些测试需要已登录、可访问的 Gemini 网页；部分测试会发送消息。
