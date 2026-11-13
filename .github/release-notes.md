下载下方 **remote-folder.exe**，双击即可使用。

普通账号登录后，点击「提升权限」可选择 sudo 或 su root。选择 su root 时输入 root 密码，文件窗口会在当前 SSH 连接中切换身份。关闭提权即可回到登录账号。

双击路径栏中的目录名即可编辑完整路径，包括显示为省略号的长路径。按 Enter 跳转，按 Esc 取消。

- 适用于 Windows 10/11 64 位（x64）。
- 已内置 Python 和应用依赖，无需安装开发环境或下载源码。
- 轻量版使用系统 WebView2。Windows 11 和绝大多数 Windows 10 已安装；缺少时可安装[微软 WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/#download)。
- 单文件便携版，无需安装。启动时会自动解压内置组件，请等待窗口出现。
- 连接配置保存在用户目录的 `.remote-folder` 中，密码使用 Windows 凭据管理器保存。
- `SHA256SUMS.txt` 可用于核对下载文件的完整性。

文件管理与传输无需外部 SSH 程序；可选的「打开 bash」功能调用 Windows 的 OpenSSH 客户端。

GitHub 自动提供的 `Source code` 压缩包是源码，直接使用程序只需下载上面的 EXE。
