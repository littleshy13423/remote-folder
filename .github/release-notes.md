下载下方的程序即可使用。Windows 下载 **remote-folder.exe**，双击运行。Linux x64 下载 **remote-folder-linux-x64**，赋予执行权限后运行：

```bash
chmod +x remote-folder-linux-x64
./remote-folder-linux-x64
```

窗口底部可以打开「监控」，查看当前 Linux 主机的 CPU 和内存占用。取消勾选即停止读取，这个选择会被记住。

删除没有权限的文件时，可以选择 sudo 或 su root 完成本次删除。打开 bash 时，如果登录密码已经保存，终端里不会再问一次。

- Windows 版适用于 Windows 10/11 64 位（x64），使用系统 WebView2。缺少时可安装[微软 WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/#download)。
- Linux 版适用于 Ubuntu 22.04 及更新的 64 位系统，使用系统 GTK 3 和 WebKitGTK。Ubuntu 22.04 可安装 `libgtk-3-0` 和 `libwebkit2gtk-4.0-37`；Ubuntu 24.04 若没有 4.0，安装 `libwebkit2gtk-4.1-0`。
- 已内置 Python 和应用依赖，无需安装开发环境或下载源码。
- 单文件便携版，无需安装。启动时会自动解压内置组件，请等待窗口出现。
- 连接配置保存在用户目录的 `.remote-folder` 中。Windows 使用凭据管理器保存密码；Linux 在有 Secret Service 时使用系统密钥环。
- `SHA256SUMS.txt` 可用于核对下载文件的完整性。

文件管理与传输无需外部 SSH 程序；可选的「打开 bash」功能调用系统里的 OpenSSH 客户端。

GitHub 自动提供的 `Source code` 压缩包是源码，直接使用程序只需下载上面的程序文件。
