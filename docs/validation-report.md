# 验证记录

更新时间：2026-09-22

## 已完成

- Python 协议域验证单元测试：2 项通过。
- Python 文件语法编译：通过。
- 真实模型手动启动探测：RankGuess 与 PARD 均完成加载，`ping` 返回两个模型版本；按用户要求未继续跑大规模效果集。
- 数据清单：RankGuess 12,000 条行引用，PARD 3,000 对行引用；清单不含口令明文。
- 扩展 JavaScript `node --check`：新增及相关脚本通过。
- 扩展清单 JSON 与 Qt UI XML：解析通过。
- 扩展打包：`node build.js --skip-translations` 成功生成 Firefox 和 Chromium 包。
- KeePassXC：MSVC 19.44/Qt 6.8.3/vcpkg Release 构建成功，生成 `KeePassXC.exe`、`keepassxc-proxy.exe` 和 `keepassxc-cli.exe`。
- 本地安装目录：`cmake --install` 与 Qt 部署成功；补齐 vcpkg 运行库后，安装目录中的 `keepassxc-cli.exe --version` 退出码为 0。
- 跨组件静态契约：`tools/check_integration_contract.py` 通过。
- 扩展限定范围 ESLint：0 个错误，11 个 `error_code` 协议字段 camelcase 警告。
- 品牌一致性：扩展三份清单、运行时常量、38 份本地化资源、45 份 KeePassXC 翻译资源、npm 包名、归档名和演示路径已统一为 `They know your passwords`；结构解析和静态契约通过。
- 新名称构建目录：`D:\tmp\TheyKnowYourPasswordsBuild` 完成 Release 编译；`D:\tmp\TheyKnowYourPasswordsDemo` 打包成功。
- 演示包品牌复核：Chromium Manifest 为 6,294 字节，`name` 和工具栏标题均为 `They know your passwords`；扩展运行时资源无旧品牌残留。
- 扩展打包可靠性：Manifest 版本写入已串行等待，演示包只选择 `they-know-your-passwords_*_chromium.zip`，消除空 Manifest 和误选旧归档的竞态。
- Git 补丁检查：无空白错误；Windows checkout 显示预期的 LF/CRLF 提示。

## 已知限制

- 效果阈值尚未从用户已有的独立结果中导入，因此除精确复用外保持 `UNKNOWN/UNCALIBRATED`。
- 完整真实模型冒烟脚本在本机冷启动超过 90 秒后人工终止，没有运行非精确 PARD beam 搜索；脚本已改为受启动上限约束。按用户要求不继续投入时间跑效果集。
- 上游扩展全量 ESLint 在 Windows checkout 上因所有上游文件均为 CRLF 而失败；JavaScript 语法检查与扩展打包已通过，新增文件另行使用忽略换行规则的 lint。
- 宿主与扩展尚未在真实 Chrome/Edge 会话中完成 Native Messaging 握手、建库保存和网站修改后的确认保存，因此当前为可构建演示原型，集成任务保持“待验收”。
