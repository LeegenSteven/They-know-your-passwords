# 模型与既有结果核对（2026-10-07）

| 模型 | SHA-256 | 核对 |
| --- | --- | --- |
| RankGuess best_rankguess_guesser_csdn.pth | 442c105becdc849880bd1b0b3fb21477063dbc4266126d825871b3f065d1f86c | 本项目与 D:/研究生/RankGuess/save 相同 |
| PARD best_model_02_csdn.pt | eb0c8c5abaf12ced38a2ec341fcc86290302e6f75677842f8f3866cfbecbc99f | 本项目与 HGN/results_demo6_glca/models 相同 |

RankGuess 既有结果在 D:/研究生/RankGuess/results；匹配 CSDN 权重/测试来源的 detail_results_model_best_rankguess_guesser_csdn__test_testword_csdn.txt 哈希为 694b8a4ae77faf73de6eee92c4a4d0f551012cee530a7ad7be487d235ce6b730。只登记来源和身份，不复制其中真实口令。已有聚合结果没有充分证明本轮 CPU 参考表 sample=100000/batch=1000/seed=20260922 与独立标定分区相符，不能从结果文件名称直接冻结阈值。

匹配 GLCA 聚合结果为 D:/研究生/HGN/results_demo6_glca/eval_results/eval_02_csdn.json，哈希 9c5671049c8d69a3ac5a4d2aa9f29d822f07c3ee7fdc2a4fa3abfdeed7db4cf6。架构 GatedLatentCrossAttention_Teacher_SourceStudent_TwoStage，student 推理、embed=256/latent=128/heads=4，测试来源 csdn_test.jsonl，总数 20000。其 Linux 记录路径与本机配套 models 权重身份已核对。

该效果集 beam=1000；本机默认 beam=100/top-k=200/batch=1、AMP、图边全部关闭。架构/权重匹配不代表搜索预算匹配，因此不把其未命中或聚合成功率转换为本机低风险阈值。D:/研究生/HGN/results/eval_02_csdn.json 属另一架构，明确排除。

现有抽样清单保留 RankGuess 12000 条与 PARD 3000 对来源文件哈希、行号、分区，排除训练重合与跨分区口令连通泄漏。未复制真实口令、未重新运行大效果集。本轮补测仅使用内存生成的虚构输入和业务闭环；专门数值回归 16 条，PARD 精确/非精确各一条，不进行大规模效果声称。

六档区间依据 FeatureGuess 论文第 9 页。数值区间展示与风险标定分开：当前 calibrated=false；截断只展示下界；PARD 未命中未知；推荐仅 REVIEW_REQUIRED。后续若冻结阈值，需使用当前完整推理配置、来源哈希和独立分区，补测仍受用户要求的 128 对 PARD/256 条 RankGuess 上限约束。
