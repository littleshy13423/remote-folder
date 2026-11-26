# remote-folder

用于浏览、传输和查阅远程文件的桌面工具。

## 设计理念

让远程目录像本地目录一样便于访问。专注于文件传输与查阅，支持使用熟悉的本地应用打开远程文件。

## 下载使用

打开 [最新 Release](https://github.com/littleshy13423/remote-folder/releases/latest)。

- Windows 10/11 64 位：下载 **remote-folder.exe**，双击运行。程序使用系统 WebView2，不把完整浏览器打进文件里。缺少时可安装[微软 WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/#download)。
- Mac（Apple 芯片，macOS 14 或更新）：下载 **remote-folder-macos-arm64.zip**，解压后打开 `remote-folder.app`。它使用系统 WebKit。首次打开若被拦截，在终端执行 `xattr -dr com.apple.quarantine remote-folder.app`。Intel 芯片的 Mac 不能用这个文件。
- Linux x64（Ubuntu 22.04 或更新）：下载 **remote-folder-linux-x64**。它使用系统的 GTK 3 和 WebKitGTK，不能在 Mac 上运行。Ubuntu 22.04 安装 `libgtk-3-0` 和 `libwebkit2gtk-4.0-37`；Ubuntu 24.04 若没有 4.0，安装 `libwebkit2gtk-4.1-0`。下载后执行 `chmod +x remote-folder-linux-x64`，再运行这个文件。
- 单个程序已包含 Python 和应用依赖，无需安装 Python、下载源码或保留 `_internal` 文件夹。
- 启动时会自动解压内置组件，等待窗口出现即可。程序会打开自己的窗口，关掉窗口就退出。
- 连接配置保存在用户目录的 `.remote-folder` 中；更新时替换程序文件即可保留配置。
- Release 中的 `Source code` 是给开发者的源码包，使用程序不需要下载。

打开目标远程目录后，点击「上传文件」可分次添加文件，勾选后一并上传。也可以直接将文件或文件夹拖入列表，嵌套目录和空文件夹都会保留。

文件管理与传输不依赖外部 SSH 程序；可选的「打开 bash」功能使用系统里的 OpenSSH 客户端，没有安装时会提示。

需要管理受限目录时，点击「提升权限」。平时使用 `su` 切换到 root 的用户，选择「su root」并输入 root 密码，即可在当前 SSH 连接中以 root 浏览和传输文件。操作完成后点击关闭提权，返回原账号。此方式要求 Linux 服务器允许该账号通过非交互终端的 su 密码认证，并已安装独立的 OpenSSH sftp-server。使用 sudo 的用户可以选择 sudo，服务器配置了免密 sudo 时可留空密码。

## 开发与打包

开发时在 Windows 上双击 `run.bat`。Windows 打包运行 `./build.ps1`，生成 `dist/remote-folder.exe`。Linux x64 打包运行 `./build.sh`，生成 `dist/remote-folder-linux-x64`。Mac 打包运行 `./build-mac.sh`，生成 `dist/remote-folder-macos-<芯片>.zip`。这些准备工作只在对应系统上执行，使用者只需下载最终程序。

## 发布新版本

GitHub Actions 会在 `main` 更新时构建 Windows 和 Linux 程序，运行应用测试，并在源码目录之外启动打包后的程序检查窗口和静态资源。

确认 Actions 中的 **Desktop builds** 成功后，为该提交创建版本标签并推送：

```bash
git tag v0.1.0
git push origin v0.1.0
```

版本标签使用 `v主版本.次版本.修订版本` 格式；后续发布请使用新的版本号。标签触发的构建通过测试后，会自动创建 GitHub Release，并上传 Windows、Linux 程序和 SHA-256 校验文件。构建失败时不会发布 Release，已有 Release 不会被覆盖。
