# remote-folder

用于浏览、传输和查阅远程文件的桌面工具。

## 设计理念

让远程目录像本地目录一样便于访问。专注于文件传输与查阅，支持使用熟悉的本地应用打开远程文件。

## 下载使用（Windows）

打开 [最新 Release](https://github.com/littleshy13423/remote-folder/releases/latest)，下载 **remote-folder.exe**，双击运行。

- 支持 Windows 10/11 64 位（x64）。
- 单个 EXE 已包含 Python 和应用依赖，无需安装 Python、下载源码或保留 `_internal` 文件夹。
- 使用系统 WebView2，不把完整浏览器运行时打进 EXE。Windows 11 和绝大多数 Windows 10 设备已安装；缺少时可安装[微软 WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/#download)。
- 启动时会自动解压内置组件，等待窗口出现即可。程序会打开自己的窗口，关掉窗口就退出。
- 连接配置保存在用户目录的 `.remote-folder` 中；更新时替换 EXE 即可保留配置。
- Release 中的 `Source code` 是给开发者的源码包，使用程序不需要下载。

打开目标远程目录后，点击「上传文件」可分次添加文件，勾选后一并上传。也可以直接将文件或文件夹拖入列表，嵌套目录和空文件夹都会保留。

文件管理与传输不依赖外部 SSH 程序；可选的「打开 bash」功能使用 Windows 的 OpenSSH 客户端，系统未安装该可选组件时会提示。

## 开发与打包

开发时在 Windows 上双击 `run.bat`。本地打包需要 Windows 和 Python 3.11 x64，在 PowerShell 中运行：

```powershell
./build.ps1
```

脚本会准备构建环境，并生成 `dist/remote-folder.exe` 和 `dist/SHA256SUMS.txt`。这些准备工作仅在构建机器上执行，使用者只需下载最终 EXE。

## 发布新版本

GitHub Actions 会在 `main` 更新时构建 Windows EXE，运行应用测试，并在源码目录之外启动打包后的程序检查窗口和静态资源。

确认 Actions 中的 **Windows EXE** 成功后，为该提交创建版本标签并推送：

```bash
git tag v0.1.0
git push origin v0.1.0
```

版本标签使用 `v主版本.次版本.修订版本` 格式；后续发布请使用新的版本号。标签触发的构建通过测试后，会自动创建 GitHub Release 并上传 EXE 和 SHA-256 校验文件。构建失败时不会发布 Release，已有 Release 不会被覆盖。
