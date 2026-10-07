# v0.2.0 — Windows 软件与 Chrome/Edge 插件

本次提供两个可使用的下载附件。下载软件包并完整解压后双击 Setup.cmd，在软件中设置自己的主口令；在 Chrome/Edge 开发者模式加载插件并批准口令库关联。以后使用 Launch.cmd 启动。

- **TKYP-Windows-x64-v0.2.0.zip**：桌面客户端、浏览器宿主、Qt/MSVC 运行库、独立 Python 3.12.10 / PyTorch 2.8.0 CPU、两模型及预构建概率参考表，无需另装 Anaconda/CUDA 或编译器。
- **TKYP-Chrome-Edge-v0.2.0.zip**：固定 ID `ijlckofhohjbbifcfhpiglkmfndaaeol` 的 Chrome/Edge 插件；插件需配合桌面软件使用。软件也附同版本 extension-chromium 文件夹。
- **SHA256SUMS.txt**：两附件的 SHA-256 校验值。Source code 附件为源码，直接使用请下载上面的软件和插件附件。

模型副本采用 engine-a.bin、engine-b.bin；只改文件名，模型哈希与原权重一致。按模型所有者要求在公开 Release 分发，改名不提供隐私保护。不包含原始数据、个人口令库、浏览器配置或密钥。

实际安装的 Chrome/Edge、桌面程序和真实 CPU 模型在含空格的新目录、清洁 PATH 下各通过 32 项闭环检查：新增落盘、重启填充、网站成功后确认、生成后切换账号拒绝保存、原目标更新、锁库拒绝，以及默认 3 秒超时返回 UNKNOWN 后的显式重试。摘要见源码 docs/release-acceptance-v0.2.0.json。

PSM 六档表示估计区间，PARD 重用独立展示；当前阈值未标定，候选保持 REVIEW_REQUIRED，未命中和截断不宣称低风险。CPU 历史复检请在可信界面主动选择 30 秒预算；历史较多或 CPU 较慢可选择 120 秒。默认 3 秒和搜索宽度保持不变。

首次加载插件、设置主口令和批准数据库关联需用户操作；本次未替用户操作个人口令库，也未验证所有其他 Windows/硬件组合。
