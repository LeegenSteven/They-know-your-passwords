# 本地口令风险算法服务

该服务以常驻 Python 进程运行，通过标准输入和标准输出交换一行一条的 UTF-8 JSON。它不监听端口，也不发起网络请求。`RankGuess拖网猜测/` 与 `PARD定向猜测/` 只读导入，服务不会修改算法源码或权重。

## 启动

```powershell
D:\Anaconda3\envs\pytorch_cuda\python.exe -u .\algo-service\server.py --config .\algo-service\config.toml
```

启动后模型在单一推理工作线程中预热。预热期间 `ping` 返回 `LOADING`；完成后返回模型能力和版本。协议详见 `docs/algorithm-contract.md`。

## 自检

```powershell
D:\Anaconda3\envs\pytorch_cuda\python.exe .\algo-service\tools\smoke_test.py
D:\Anaconda3\envs\pytorch_cuda\python.exe -m unittest discover -s .\algo-service\tests -v
```

自检从已授权数据的固定有效行读取输入，但不输出输入内容。首次自检会在 CPU 上以固定种子建立 100,000 样本的 RankGuess 参考表。缓存只包含概率与猜测次数数组，并绑定模型 SHA-256、采样配置、适配器版本和 PyTorch 版本。

## 性能记录

| 操作 | 目标 | 当前实测 |
| --- | --- | --- |
| 服务冷启动（缓存未命中） | GPU ≤ 60 秒 / CPU ≤ 120 秒 | 待测 |
| 服务冷启动（缓存命中） | ≤ 15 秒 | 待测 |
| `assess_trawling` 热请求 | ≤ 150 毫秒 | 待测 |
| `assess_reuse` 四条历史 | ≤ 800 毫秒 | 待测 |

目标用于发现退化，不代表未经实测的性能承诺。

## 安全约束

- stdout 仅用于协议 JSON，算法原有输出全部重定向到 stderr。
- 错误日志只记录异常类型，不记录请求参数。
- 原始口令、历史集合和候选口令不写入缓存、日志或报告。
- 未知、越域、超时和模型不可用均返回明确状态，不生成默认风险等级。
