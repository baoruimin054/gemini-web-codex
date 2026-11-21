# Gemini Codex Web

在 Windows 上使用 Codex 桌面或 CLI，通过本机网关将请求交给 Ego Lite 中已登录的 Gemini 网页。项目使用你自己的 Gemini 网页会话和额度。可用模型、响应速度及地区支持由 Gemini 网页决定。

## 安装

1. 安装并打开 Windows 版 Codex、Ego Lite，以及带 Tkinter 的 Python 3.12 或更新版本。
2. 在 Ego Lite 的「用户配置 1」登录 Gemini，并确认能直接打开 Gemini 聊天页。本项目默认使用该配置（浏览器标识 `Profile 1`）。
3. 解压发布包到准备长期使用的目录，从项目根目录运行以下 PowerShell 命令。安装程序会创建项目自己的 Python 环境和桌面快捷方式。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Install.ps1
```

4. 在桌面打开 **Gemini Codex Web**。使用 CLI 时运行 `.\Gemini-Codex.cmd`，并确保 `codex` 在 PATH 中。

本地运行数据位于 `%LOCALAPPDATA%\CodexGeminiWeb`。首次启动会生成本地网关凭据和 Codex 配置。已有安装会继续使用该目录里的设置。运行 `.\Gemini-Desktop.cmd` 可以在终端中查看启动错误。

## 运行方式

- 网关只监听 `127.0.0.1:18473`。Codex 发送请求前会读取本机生成的网关凭据。
- Gemini 对话使用独立的 Ego 空间。关闭专用 Codex 窗口会停止网关并关闭对应空间，保存的 Gemini 对话地址用于下次续接。
- Codex 按本机的权限配置执行工具。为回答任务而交给模型的提示词或工具结果会通过当前 Gemini 网页发送。
- 发布包包含源码、安装脚本和测试。Codex、Ego Lite、Python、浏览器登录状态与运行数据由本机安装和生成。

## 本地验证

以下检查无需启动 Gemini 网页。JavaScript 测试需要 Node.js。

```powershell
.\.artifact-venv\Scripts\python.exe -m unittest test_lifecycle test_watchdog test_bridge_steps test_resume
node --test test_structured_reply.mjs
```

仓库中另有真人会话集成测试。这些测试需要已登录、可访问的 Gemini 网页；部分测试会发送消息。
