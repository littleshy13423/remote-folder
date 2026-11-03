# remote-folder

浏览和传输远程目录的桌面程序，用来替代 MobaXterm 左侧的文件栏。不内置文本编辑器。

## 能做什么

- 保存 SSH/SFTP 连接。密码放在 Windows 凭据管理器里，不写进项目文件
- 浏览远程目录，上传、下载、新建文件夹、重命名、删除
- 把文件或文件夹拖进窗口上传
- 双击文件时，按 Windows 里该后缀的默认程序打开
- 右键「打开方式」可以换一个程序；勾选「始终使用此应用打开」后，这个后缀以后都会用它
- 在本机程序里保存后，改动会写回远程

## 启动

开发时双击 `run.bat`。程序会打开自己的窗口，不经过浏览器。关掉窗口，程序就退出。

发给别人时，先在本机运行 `build.ps1`，得到 `dist\remote-folder-windows.zip`。对方解压后双击其中的 `remote-folder.exe`，不需要安装 Python。请保留 exe 旁边的 `_internal` 文件夹。Windows 10/11 需要系统里已有的 Edge WebView2。
