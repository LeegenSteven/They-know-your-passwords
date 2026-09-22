# Windows 构建与演示

## 已验证工具版本

- Visual Studio Build Tools 2022 17.14.41，MSVC 19.44.35229。
- CMake 3.29.2，Ninja 1.12.0。
- Qt 6.8.3 `win64_msvc2022_64`，含 Declarative、ImageFormats、SVG、Tools 和 Translations。
- Python 3.9.25，PyTorch 2.8.0+cu129，torch-geometric 2.6.1，tomli 2.2.1。

## 配置桌面客户端

Ninja 在 Windows 上无法可靠扫描含中文字符的源码路径。先创建只指向同一工作区源码的 ASCII 目录联接，再在 Visual Studio x64 Developer Command Prompt 中执行：

```powershell
New-Item -ItemType Junction -Path D:\tmp\TheyKnowYourPasswordsSrc -Target 'D:\研究生\网络安全竞赛\desktop-app'
cmake -S D:\tmp\TheyKnowYourPasswordsSrc -B D:\tmp\TheyKnowYourPasswordsBuild -G Ninja `
  -DCMAKE_BUILD_TYPE=Release `
  -DCMAKE_TOOLCHAIN_FILE=D:\vcpkg\scripts\buildsystems\vcpkg.cmake `
  -DCMAKE_PREFIX_PATH=D:\Qt\6.8.3\msvc2022_64 `
  -DVCPKG_TARGET_TRIPLET=x64-windows `
  -DWITH_TESTS=OFF -DKPXC_FEATURE_DOCS=OFF `
  -DKPXC_FEATURE_NETWORK=OFF -DKPXC_FEATURE_UPDATES=OFF `
  -DKPXC_FEATURE_SSHAGENT=OFF
cmake --build D:\tmp\TheyKnowYourPasswordsBuild --target KeePassXC keepassxc-proxy keepassxc-cli -j 4
```

关闭 KeePassXC 的网络功能不影响 Native Messaging，本地命名管道仍使用 Qt Network。

## 配置算法服务

模型文件保留在原算法目录，不复制到仓库。可用环境变量启动：

```powershell
$env:KEEPASSXC_RISK_PYTHON='D:\Anaconda3\envs\pytorch_cuda\python.exe'
$env:KEEPASSXC_RISK_SERVICE='D:\研究生\网络安全竞赛\algo-service\server.py'
$env:KEEPASSXC_RISK_CONFIG='D:\研究生\网络安全竞赛\algo-service\config.toml'
& 'D:\tmp\TheyKnowYourPasswordsBuild\src\TheyKnowYourPasswords.exe'
```

也可在 KeePassXC“设置 → 浏览器集成 → 高级”填写同样的 Python、服务脚本和 TOML 路径。启动时服务会预热模型；stdout 只允许协议 JSON，宿主丢弃 stderr，避免诊断信息进入应用日志。

仓库也提供 `scripts\start-demo.ps1`。它设置上述三个环境变量后启动 `D:\tmp\TheyKnowYourPasswordsPackage\TheyKnowYourPasswords.exe`。

## 加载扩展

扩展构建命令：

```powershell
cd browser-extension
npm install
node build.js --skip-translations
```

运行 `npm run debug:chromium` 可把带固定公钥的 Chromium 清单复制到开发目录。随后在 Chrome 或 Edge 的扩展管理页面启用开发者模式，选择“加载已解压的扩展”，目录为 `browser-extension\extension`。固定 ID 应显示为 `ijlckofhohjbbifcfhpiglkmfndaaeol`。在桌面客户端的浏览器设置中启用 Chrome/Edge 后重新生成 Native Messaging 清单。

若要生成包含 Qt/vcpkg 运行库、算法服务和已解压 Chromium 扩展的本地演示目录，执行：

```powershell
.\scripts\package-demo.ps1 -BuildDirectory D:\tmp\TheyKnowYourPasswordsBuild -OutputDirectory D:\tmp\TheyKnowYourPasswordsPackage
```

模型权重不会复制进演示目录；生成的配置仍引用工作区中的只读算法资产。加载 `D:\tmp\TheyKnowYourPasswordsPackage\extension-chromium` 后，运行 `D:\tmp\TheyKnowYourPasswordsPackage\start-demo.ps1`。

## 演示流程

1. 启动 KeePassXC，打开或创建演示 KDBX，并完成扩展关联。
2. 打开虚构账号的 HTTPS 修改口令页，点击扩展图标。
3. 在“口令风险评估”中选择场景，输入候选并评估；未标定结果显示为未知，精确复用显示高风险。
4. 点击“安全推荐”。候选经宿主生成并完成必要复检后填入页面。
5. 先在网站提交修改。只有网站确认成功后，勾选“我确认网站已成功修改口令”，再点击“确认并更新 KeePassXC”。
6. 锁定口令库并刷新面板，展示 `STALE_DATABASE_SESSION` 或数据库未打开状态；停止算法服务可展示 `UNAVAILABLE`；越域输入展示 `OUT_OF_DOMAIN`。

正式演示前使用虚构账号。不要在终端、截图或录屏中展示候选、历史口令、主密钥或派生密钥。
