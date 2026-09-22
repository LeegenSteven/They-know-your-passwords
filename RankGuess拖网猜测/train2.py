"""
RankGuess: Password Guessing Using Adversarial Ranking
复现自论文: Yang & Wang, IEEE S&P 2025

【修复说明 v2 — 对应代码审查报告中的 5 项偏差】

修复1 (严重): train_ranker_one_iter 中 expert_feat 从未参与计算
  论文依据: Eq.5 / Eq.9 / Algorithm 1 Line.10
    T_{θ,φ}(SM_i, E) 排名分必须基于与 expert E 的 cosine 相似度
  修复方案: 对每组 mixup 特征调用 cosine_ranking_score(mix_feat, expert_feat, s_plus_feat)
            以同批次真实密码 (real_feat) 作为 Eq.5 分母中 S⁺ 的贡献

修复2 (严重): cosine_ranking_score 分母缺少 S⁺
  论文依据: Eq.5
    P(PW|E,S⁺) = exp[cos(y_PW,y_E)] / Σ_{PW'∈S⁺∪{PW}} exp[cos(y_PW',y_E)]
  修复方案: 添加可选参数 s_plus_feat；传入时将其 exp(cos) 之和加入分母

修复3 (中等): 对抗训练内层循环结构不符论文
  论文依据: Algorithm 1 Line 7-17
    先执行 K 次 Ranker 更新 (Line 7-11)，再执行 K 次 Guesser 更新 (Line 14-17)
  修复方案: 将单 for-k 循环中的交替更新拆为两个独立 for-k 循环

修复4 (Bug): train_guesser_per_step 中存在冗余且无效的 GRU 前向调用
  位置: 原 "emb, _ = guesser.gru(guesser.embedding(prefix))" 行
  修复方案: 直接删除该行，保留第二次调用以获取 h_pref

修复5 (注释): KL 方向注释写反（代码逻辑本身正确）
  原注释: KL(D_rank ‖ D_lambda)
  实际计算: F.kl_div(log(d_rank), d_lambda) = KL(D_lambda ‖ D_rank)
  论文 Fig.3: KL(λ ‖ D_R)，与代码一致
  修复方案: 注释改为 KL(D_lambda ‖ D_rank)

【论文参数严格对齐 Section 4.1】:
  - optimizer  : Adam, lr=0.001, betas=(0.5, 0.999)
  - penalty γ  : 0.1
  - pretrain   : 5 epochs
  - total      : 20 epochs
  - group_num  : 17
  - dropout    : 0.3
  - Guesser    : GRU
  - Ranker     : GRU (similar structure)
  - S⁻         : 始终由 Gθ 生成 (Algorithm 1 Line 3/8)
  - Expert E   : top-1w (Section 3.2)

使用方法:
  python rankguess_train_fixed.py --train_file <path> [--save_dir save/]
"""

import os
import argparse
import random
import numpy as np
from collections import Counter
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
from tqdm import tqdm


def convert_windows_path(win_path: str) -> str:
    """
    将Windows原生文件路径（带反斜杠\）转换为程序可读取的标准路径
    支持三种输出格式：正斜杠/、原始字符串r""、双反斜杠\\
    """
    # 方式1：最通用 → 替换为正斜杠 /（所有编程语言都支持）
    converted_path = win_path.replace("\\", "/")

    # 方式2：如果你需要Python原始字符串格式（返回带 r"" 的字符串）
    # converted_path = fr"{win_path}"

    return converted_path

# =============================================================================
# 1. 全局配置
# =============================================================================
class Config:
    ASCII_START = 32
    ASCII_END   = 126
    CHARS       = [chr(i) for i in range(ASCII_START, ASCII_END + 1)]
    SOS_TOKEN   = "<SOS>"
    EOS_TOKEN   = "<EOS>"
    PAD_TOKEN   = "<PAD>"
    CHARS.extend([SOS_TOKEN, EOS_TOKEN, PAD_TOKEN])
    CHAR2IDX    = {c: i for i, c in enumerate(CHARS)}
    IDX2CHAR    = {i: c for i, c in enumerate(CHARS)}
    VOCAB_SIZE  = len(CHARS)          # 98
    SOS_IDX     = CHAR2IDX[SOS_TOKEN]
    EOS_IDX     = CHAR2IDX[EOS_TOKEN]
    PAD_IDX     = CHAR2IDX[PAD_TOKEN]

    MIN_LEN = 5
    MAX_LEN = 20

    # ── 模型超参数 ──
    EMBED_DIM   = 64
    HIDDEN_SIZE = 256
    NUM_LAYERS  = 3

    # ── 训练超参数 (严格对齐论文 Section 4.1) ──
    TRAIN_FILE = convert_windows_path(r"D:\研究生\Password_Dataset\Password_Dataset\trainword_tianya.txt")
    DROPOUT         = 0.3           # 论文: "0.3 dropout rate"
    LR              = 1e-3          # 论文: "initial learning rate of 0.001"
    ADAM_BETAS      = (0.5, 0.999)  # 论文: "betas of (0.5, 0.999)"
    PENALTY_GAMMA   = 0.1           # 论文: "penalty γ = 0.1"
    PRETRAIN_EPOCHS = 5             # 论文: "five pre-train epochs"
    TOTAL_EPOCHS    = 20            # 论文: "a total of 20 epochs"
    GROUP_NUM       = 17            # 论文: "17 group numbers"
    BATCH_SIZE      = 256
    K_ITER          = 5             # 对抗训练内部迭代次数

    # MC rollout 超参数
    MC_ROLLOUT_N    = 5             # per-step rollout 次数
    REINFORCE_N     = 256           # 序列级 REINFORCE 采样数 (推荐模式)

    # Expert E: top-1w 密码 (论文 Section 3.2)
    TOP_EXPERT_K    = 10000

    # S⁺ 分母批次大小 (用于 Eq.5 归一化)
    S_PLUS_DENOM_N  = 256

    VAL_RATIO       = 0.05
    SAVE_DIR        = "save"

    # 奖励方差守卫阈值
    REWARD_STD_MIN  = 1e-4


cfg = Config()


# =============================================================================
# 2. 数据加载
# =============================================================================
def load_passwords(path: str) -> list:
    passwords = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            p = line.strip()
            if (cfg.MIN_LEN <= len(p) <= cfg.MAX_LEN
                    and all(cfg.ASCII_START <= ord(c) <= cfg.ASCII_END
                            for c in p)):
                passwords.append(p)
    return passwords


def encode_password(pwd: str) -> torch.Tensor:
    idxs = ([cfg.SOS_IDX]
            + [cfg.CHAR2IDX[c] for c in pwd if c in cfg.CHAR2IDX]
            + [cfg.EOS_IDX])
    return torch.tensor(idxs, dtype=torch.long)


class PasswordDataset(Dataset):
    def __init__(self, passwords: list):
        self.data = passwords

    def __len__(self):
        return len(self.data)

    def __getitem__(self, idx):
        full = encode_password(self.data[idx])
        T    = cfg.MAX_LEN + 1
        inp  = torch.full((T,), cfg.PAD_IDX, dtype=torch.long)
        tgt  = torch.full((T,), cfg.PAD_IDX, dtype=torch.long)
        L    = min(len(full) - 1, T)
        inp[:L] = full[:L]
        tgt[:L] = full[1:L + 1]
        return inp, tgt


def pad_sequences(seqs: list, pad_idx: int = cfg.PAD_IDX) -> torch.Tensor:
    max_len = max(s.size(0) for s in seqs)
    out = torch.full((len(seqs), max_len), pad_idx, dtype=torch.long)
    for i, s in enumerate(seqs):
        out[i, :s.size(0)] = s
    return out


# =============================================================================
# 3. 模型定义
# =============================================================================
class GuesserModel(nn.Module):
    """
    GRU-based 密码生成器 (论文 Eq.10-11).
    "we use GRU as the guesser model Gθ"
    """
    def __init__(self):
        super().__init__()
        self.vocab_size  = cfg.VOCAB_SIZE
        self.hidden_size = cfg.HIDDEN_SIZE
        self.num_layers  = cfg.NUM_LAYERS
        self.embed_dim   = cfg.EMBED_DIM

        self.embedding  = nn.Embedding(cfg.VOCAB_SIZE, cfg.EMBED_DIM,
                                        padding_idx=cfg.PAD_IDX)
        self.gru = nn.GRU(
            input_size  = cfg.EMBED_DIM,
            hidden_size = cfg.HIDDEN_SIZE,
            num_layers  = cfg.NUM_LAYERS,
            batch_first = True,
            dropout     = cfg.DROPOUT if cfg.NUM_LAYERS > 1 else 0.0,
        )
        self.layer_norm = nn.LayerNorm(cfg.HIDDEN_SIZE)
        self.fc1        = nn.Linear(cfg.HIDDEN_SIZE, cfg.HIDDEN_SIZE)
        self.fc2        = nn.Linear(cfg.HIDDEN_SIZE, cfg.VOCAB_SIZE)
        self.activation = nn.GELU()
        self.dropout    = nn.Dropout(cfg.DROPOUT)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x: (B, T) → logits: (B, T, V)"""
        emb      = self.embedding(x)
        out, _   = self.gru(emb)
        out      = self.layer_norm(out)
        residual = out
        out      = self.activation(self.fc1(out))
        out      = self.dropout(out)
        out      = out + residual
        return self.fc2(out)

    def forward_step(self, x_t: torch.Tensor, h: torch.Tensor):
        """单步推理: x_t (B,1), h (L,B,H) → logits (B,V), h"""
        emb      = self.embedding(x_t)
        out, h   = self.gru(emb, h)
        out      = self.layer_norm(out)
        residual = out
        out      = self.activation(self.fc1(out))
        out      = out + residual
        return self.fc2(out[:, 0, :]), h


class RankerModel(nn.Module):
    """
    GRU-based 密码排名评分器 (论文 Section 3.4).
    "The proposed password ranker Rφ shares a similar recurrent
     neural network structure."
    """
    def __init__(self):
        super().__init__()
        self.vocab_size  = cfg.VOCAB_SIZE
        self.hidden_size = cfg.HIDDEN_SIZE
        self.num_layers  = cfg.NUM_LAYERS

        self.embedding  = nn.Embedding(cfg.VOCAB_SIZE, cfg.EMBED_DIM,
                                        padding_idx=cfg.PAD_IDX)
        self.gru = nn.GRU(
            input_size  = cfg.EMBED_DIM,
            hidden_size = cfg.HIDDEN_SIZE,
            num_layers  = cfg.NUM_LAYERS,
            batch_first = True,
            dropout     = cfg.DROPOUT if cfg.NUM_LAYERS > 1 else 0.0,
        )
        self.layer_norm = nn.LayerNorm(cfg.HIDDEN_SIZE)
        self.fc1        = nn.Linear(cfg.HIDDEN_SIZE, cfg.HIDDEN_SIZE)
        self.fc2        = nn.Linear(cfg.HIDDEN_SIZE, 1)
        self.activation = nn.GELU()
        self.dropout    = nn.Dropout(cfg.DROPOUT)

    def get_feature(self, x: torch.Tensor) -> torch.Tensor:
        """
        提取特征向量 y_s (论文 Eq.5 中的 y_PW / y_E).
        x: (B, T) → feat: (B, H)
        """
        emb    = self.embedding(x)
        out, _ = self.gru(emb)
        out    = self.layer_norm(out)
        mask   = (x != cfg.PAD_IDX).float().unsqueeze(-1)   # (B,T,1)
        pooled = (out * mask).sum(1) / mask.sum(1).clamp(min=1)
        return pooled                                         # (B, H)

    def get_feature_from_emb(self, emb: torch.Tensor) -> torch.Tensor:
        """
        从已有 embedding 张量提取特征 (用于 Mixup).
        emb: (B, T, E) → feat: (B, H)
        """
        out, _ = self.gru(emb)
        out    = self.layer_norm(out)
        return out.mean(1)                                    # (B, H)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """x: (B, T) → score: (B,)"""
        feat = self.get_feature(x)
        h    = self.activation(self.fc1(feat))
        h    = self.dropout(h)
        return self.fc2(h).squeeze(-1)

    def score_from_emb(self, emb: torch.Tensor) -> torch.Tensor:
        """
        直接从 embedding 计算排名分 (用于 Mixup, 避免重复 embedding lookup).
        emb: (B, T, E) → score: (B,)
        """
        feat = self.get_feature_from_emb(emb)
        h    = self.activation(self.fc1(feat))
        h    = self.dropout(h)
        return self.fc2(h).squeeze(-1)


# =============================================================================
# 4. 排名分 (论文 Eq.5) — 【修复2】加入 S⁺ 分母贡献
# =============================================================================
def cosine_ranking_score(pw_feat:     torch.Tensor,
                          expert_feat: torch.Tensor,
                          s_plus_feat: torch.Tensor = None) -> torch.Tensor:
    """
    论文 Eq.5:
      P(PW|E,S⁺) = exp[cos(y_PW,y_E)]
                   / Σ_{PW'∈S⁺∪{PW}} exp[cos(y_PW',y_E)]

    【修复2】原实现仅对生成批次求和 (相当于 S⁺ 为空)，导致正负样本
    无法形成对比。现在可选传入 s_plus_feat，将真实密码的 exp(cos) 之
    和加入分母，与 Eq.5 保持一致。

    参数:
      pw_feat:     (B, H)  待评分密码特征 (可为 S⁻ 或 mixup 样本)
      expert_feat: (H,)    expert E 特征
      s_plus_feat: (N, H)  可选，S⁺ 真实密码特征；None 时退化为原始实现

    返回:
      score: (B,) ∈ (0, 1)
    """
    cos_sim  = F.cosine_similarity(
        pw_feat,
        expert_feat.unsqueeze(0).expand_as(pw_feat),
        dim=-1)
    exp_sim  = torch.exp(cos_sim)                            # (B,)

    if s_plus_feat is not None:
        sp_cos     = F.cosine_similarity(
            s_plus_feat,
            expert_feat.unsqueeze(0).expand_as(s_plus_feat),
            dim=-1)
        # S⁺ 贡献作为分母的固定部分，detach 以稳定训练
        sp_exp_sum = torch.exp(sp_cos).sum().detach()        # scalar
        denom      = exp_sim.sum() + sp_exp_sum + 1e-9
    else:
        denom = exp_sim.sum() + 1e-9

    return exp_sim / denom


# =============================================================================
# 5. 批量生成密码 (带 log_prob)
# =============================================================================
@torch.no_grad()
def batch_generate(guesser:     GuesserModel,
                   n:           int,
                   device,
                   temperature: float = 1.0) -> list:
    """批量自回归采样, 返回密码字符串列表."""
    guesser.eval()
    results    = []
    batch_size = min(512, n)

    while len(results) < n:
        cur_bs = min(batch_size, n - len(results))
        h    = torch.zeros(cfg.NUM_LAYERS, cur_bs, cfg.HIDDEN_SIZE, device=device)
        curr = torch.full((cur_bs, 1), cfg.SOS_IDX, dtype=torch.long, device=device)
        chars = [[] for _ in range(cur_bs)]
        done  = torch.zeros(cur_bs, dtype=torch.bool, device=device)

        for _ in range(cfg.MAX_LEN + 1):
            logits, h = guesser.forward_step(curr, h)
            probs = F.softmax(logits / temperature, dim=-1)
            probs[:, cfg.SOS_IDX] = 0.0
            probs[:, cfg.PAD_IDX] = 0.0
            too_short = torch.tensor(
                [len(c) < cfg.MIN_LEN for c in chars], device=device)
            probs[too_short, cfg.EOS_IDX] = 0.0
            probs = probs / probs.sum(dim=-1, keepdim=True).clamp(min=1e-9)
            nxt = torch.multinomial(probs, 1)
            for i in range(cur_bs):
                if done[i]:
                    continue
                tok = nxt[i, 0].item()
                if tok == cfg.EOS_IDX:
                    done[i] = True
                else:
                    chars[i].append(cfg.IDX2CHAR[tok])
                    if len(chars[i]) >= cfg.MAX_LEN:
                        done[i] = True
            curr = nxt
            if done.all():
                break
        for c in chars:
            results.append("".join(c))

    guesser.train()
    return results[:n]


def sample_with_log_probs(guesser: GuesserModel,
                           n:       int,
                           device) -> tuple:
    """
    采样 n 条完整密码, 同时返回每条密码的 log P(x) (保留计算图).
    用于序列级 REINFORCE.

    返回:
      passwords:     list[str]    长度 n
      seq_log_probs: Tensor (n,)  每条密码的 log P(x), 含梯度
    """
    guesser.train()
    batch_size = min(256, n)
    all_passwords    = []
    all_seq_log_prob = []

    while len(all_passwords) < n:
        cur_bs = min(batch_size, n - len(all_passwords))
        h    = torch.zeros(cfg.NUM_LAYERS, cur_bs, cfg.HIDDEN_SIZE, device=device)
        curr = torch.full((cur_bs, 1), cfg.SOS_IDX, dtype=torch.long, device=device)

        chars         = [[] for _ in range(cur_bs)]
        done          = torch.zeros(cur_bs, dtype=torch.bool, device=device)
        step_log_prob = []

        for _ in range(cfg.MAX_LEN + 1):
            logits, h = guesser.forward_step(curr, h)
            probs = F.softmax(logits, dim=-1)
            probs_det = probs.detach().clone()
            probs_det[:, cfg.SOS_IDX] = 0.0
            probs_det[:, cfg.PAD_IDX] = 0.0
            too_short = torch.tensor(
                [len(c) < cfg.MIN_LEN for c in chars], device=device)
            probs_det[too_short, cfg.EOS_IDX] = 0.0
            probs_det = probs_det / probs_det.sum(dim=-1, keepdim=True).clamp(min=1e-9)

            nxt = torch.multinomial(probs_det, 1)
            log_pi = F.log_softmax(logits, dim=-1)
            step_lp = log_pi.gather(1, nxt).squeeze(1)

            mask_lp = step_lp * (~done).float()
            step_log_prob.append(mask_lp)

            for i in range(cur_bs):
                if done[i]:
                    continue
                tok = nxt[i, 0].item()
                if tok == cfg.EOS_IDX:
                    done[i] = True
                else:
                    chars[i].append(cfg.IDX2CHAR[tok])
                    if len(chars[i]) >= cfg.MAX_LEN:
                        done[i] = True
            curr = nxt
            if done.all():
                break

        seq_lp = torch.stack(step_log_prob, dim=1).sum(dim=1)
        all_passwords.extend(["".join(c) for c in chars])
        all_seq_log_prob.append(seq_lp)

    all_seq_log_prob = torch.cat(all_seq_log_prob, dim=0)[:n]
    return all_passwords[:n], all_seq_log_prob


# =============================================================================
# 6. Step 1a: Guesser MLE 预训练
# =============================================================================
def pretrain_guesser(guesser:      GuesserModel,
                     train_loader: DataLoader,
                     device,
                     optimizer:    optim.Optimizer):
    """Algorithm 1 Line 2: Pre-train Gθ using MLE on D"""
    print("\n[Step 1a] Guesser MLE 预训练 ...")
    criterion = nn.CrossEntropyLoss(ignore_index=cfg.PAD_IDX)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=cfg.PRETRAIN_EPOCHS)

    guesser.train()
    for epoch in range(1, cfg.PRETRAIN_EPOCHS + 1):
        total_loss = 0.0
        for inp, tgt in tqdm(train_loader,
                             desc=f"  Pretrain G {epoch}/{cfg.PRETRAIN_EPOCHS}",
                             leave=False):
            inp, tgt = inp.to(device), tgt.to(device)
            optimizer.zero_grad()
            logits = guesser(inp)
            loss   = criterion(logits.reshape(-1, cfg.VOCAB_SIZE),
                               tgt.reshape(-1))
            loss.backward()
            nn.utils.clip_grad_norm_(guesser.parameters(), 5.0)
            optimizer.step()
            total_loss += loss.item()
        scheduler.step()
        print(f"    Epoch {epoch}/{cfg.PRETRAIN_EPOCHS}  "
              f"loss={total_loss / len(train_loader):.4f}")


# =============================================================================
# 7. Step 1b: Ranker 预训练
# =============================================================================
def pretrain_ranker(ranker:     RankerModel,
                    train_pwds: list,
                    fake_pwds:  list,
                    device,
                    optimizer:  optim.Optimizer):
    """
    Algorithm 1 Line 3-4.
    S⁻ = Gθ 生成; 损失 = BCE (真实=1, 生成=0).
    """
    print("\n[Step 1b] Ranker 预训练  [S⁻ 来自 Gθ] ...")
    scheduler = optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=cfg.PRETRAIN_EPOCHS)

    n_sample = min(len(train_pwds), len(fake_pwds), 50000)
    pos_pool  = random.sample(train_pwds, n_sample)
    neg_pool  = random.sample(fake_pwds,  n_sample)

    ranker.train()
    for epoch in range(1, cfg.PRETRAIN_EPOCHS + 1):
        combined = [(1, p) for p in pos_pool] + [(0, p) for p in neg_pool]
        random.shuffle(combined)
        total_loss = 0.0
        n_batches  = 0

        for i in range(0, len(combined), cfg.BATCH_SIZE):
            batch  = combined[i:i + cfg.BATCH_SIZE]
            labels = torch.tensor([x[0] for x in batch],
                                   dtype=torch.float, device=device)
            seqs   = [encode_password(x[1]) for x in batch]
            padded = pad_sequences(seqs).to(device)
            optimizer.zero_grad()
            scores = ranker(padded)
            loss   = F.binary_cross_entropy_with_logits(scores, labels)
            loss.backward()
            nn.utils.clip_grad_norm_(ranker.parameters(), 5.0)
            optimizer.step()
            total_loss += loss.item()
            n_batches  += 1

        scheduler.step()
        avg = total_loss / max(n_batches, 1)
        print(f"    Epoch {epoch}/{cfg.PRETRAIN_EPOCHS}  loss={avg:.4f}")


# =============================================================================
# 8. Step 2: 训练 Ranker (Algorithm 1 Line 7-11) — 【修复1 + 修复2】
# =============================================================================
def train_ranker_one_iter(ranker:      RankerModel,
                           opt_ranker:  optim.Optimizer,
                           real_pwds:   list,
                           fake_pwds:   list,
                           expert_feat: torch.Tensor,
                           device) -> float:
    """
    论文 Eq.9 / Algorithm 1 Line 7-11:

      对 N=17 个 Mixup 组 (λ_0=0, …, λ_16=1):
        SM_i = λ_i * S⁺ + (1-λ_i) * S⁻
        mean_score_i = mean( P(SM_i | E, S⁺) )   ← 论文 Eq.5

      D_rank   = softmax([mean_score_0, …, mean_score_16])
      D_lambda = normalize(λ_i + 1/N)              ← 防 log(0)

      损失 = KL(D_lambda ‖ D_rank) + γ * mean(group_scores²)

    【修复1】原代码 score_from_emb 直接输出标量，expert_feat 从未参与
      → 现改为 cosine_ranking_score(mix_feat, expert_feat, s_plus_feat)
        以符合 Eq.5 的 cosine 相似度排名形式

    【修复2】原代码分母仅对当前批次求和，S⁺ 缺失
      → 传入 real_feat (detach) 作为 Eq.5 分母中 S⁺ 的贡献

    【修复5】KL 方向注释:
      F.kl_div(log(p), q) 计算 KL(q ‖ p)，即 KL(D_lambda ‖ D_rank)
      与论文 Fig.3 标注的 KL(λ ‖ D_R) 一致
    """
    ranker.train()

    lambdas  = np.linspace(0.0, 1.0, cfg.GROUP_NUM)
    n_sample = min(len(real_pwds), len(fake_pwds),
                   cfg.BATCH_SIZE // 2)

    real_b = random.sample(real_pwds, n_sample)
    fake_b = random.sample(fake_pwds, min(n_sample, len(fake_pwds)))
    real_seqs = [encode_password(p) for p in real_b]
    fake_seqs = [encode_password(p) for p in fake_b]
    real_pad  = pad_sequences(real_seqs).to(device)
    fake_pad  = pad_sequences(fake_seqs).to(device)

    # ── Embedding (单次 lookup) ──
    real_emb = ranker.embedding(real_pad)
    fake_emb = ranker.embedding(fake_pad)
    min_T    = min(real_emb.size(1), fake_emb.size(1))
    real_emb = real_emb[:, :min_T, :]
    fake_emb = fake_emb[:, :min_T, :]

    # ── 【修复2】预计算 S⁺ 特征用于 Eq.5 分母
    #    detach: S⁺ 作为固定参考，不需要通过其反传梯度
    with torch.no_grad():
        real_feat = ranker.get_feature_from_emb(real_emb)   # (B, H)

    # ── 对 17 个 λ 分别计算 Eq.5 排名分的组内均值 ──
    group_mean_scores = []
    for lam in lambdas:
        lam_t   = float(lam)
        mix_emb = lam_t * real_emb + (1.0 - lam_t) * fake_emb
        # 【修复1】提取 mixup 特征后用 cosine_ranking_score 与 expert 对比
        mix_feat = ranker.get_feature_from_emb(mix_emb)     # (B, H), 含梯度
        # 【修复2】s_plus_feat=real_feat 将 S⁺ 加入 Eq.5 分母
        scores   = cosine_ranking_score(mix_feat, expert_feat,
                                        s_plus_feat=real_feat)  # (B,)
        group_mean_scores.append(scores.mean())

    group_scores = torch.stack(group_mean_scores)            # (17,) 含梯度

    # ── D_rank: 17 个均值分 softmax 后的分布 ──
    d_rank = F.softmax(group_scores, dim=0)                  # (17,)

    # ── D_lambda: λ 归一化分布 (目标: λ 越大→越真实→期望分越高) ──
    lam_tensor = torch.tensor(lambdas, dtype=torch.float, device=device)
    lam_tensor = lam_tensor + 1.0 / cfg.GROUP_NUM            # 防 log(0)
    d_lambda   = lam_tensor / lam_tensor.sum()               # (17,)

    # ── 【修复5】KL(D_lambda ‖ D_rank)，与论文 Fig.3 KL(λ‖D_R) 一致 ──
    kl_loss = F.kl_div(
        torch.log(d_rank + 1e-9),
        d_lambda,
        reduction="sum")

    penalty    = cfg.PENALTY_GAMMA * (group_scores ** 2).mean()
    total_loss = kl_loss + penalty

    opt_ranker.zero_grad()
    total_loss.backward()
    nn.utils.clip_grad_norm_(ranker.parameters(), 5.0)
    opt_ranker.step()

    return total_loss.item()


# =============================================================================
# 9. Step 2: 训练 Guesser (Algorithm 1 Line 12-17) — 【修复2: 加入 S⁺】
# =============================================================================
def train_guesser_reinforce(guesser:     GuesserModel,
                             ranker:      RankerModel,
                             opt_guesser: optim.Optimizer,
                             expert_feat: torch.Tensor,
                             real_pwds:   list,
                             device,
                             n_samples:   int = None) -> float:
    """
    序列级 REINFORCE (替代 per-step MC rollout，更稳定高效).

    等价于论文 Eq.8 在 t=T (整条轨迹) 时的情形:
      ∇_θ L_θ = E[ (R(τ) - b) · Σ_t log π_θ(a_t|s_t) ]
    其中 R(τ) = P(PW|E,S⁺) 来自论文 Eq.5。

    【修复2】使用 real_pwds 采样 S⁺ 批次，传入 cosine_ranking_score
            以正确归一化奖励，与 Eq.5 保持一致。
    """
    if n_samples is None:
        n_samples = cfg.REINFORCE_N

    guesser.train()
    ranker.eval()

    passwords, seq_log_probs = sample_with_log_probs(
        guesser, n_samples, device)

    with torch.no_grad():
        # 生成密码特征
        seqs   = [encode_password(p) for p in passwords]
        padded = pad_sequences(seqs).to(device)
        pw_feat = ranker.get_feature(padded)                 # (N, H)

        # 【修复2】采样 S⁺ 特征，加入 Eq.5 分母
        s_plus_sample = random.sample(
            real_pwds, min(cfg.S_PLUS_DENOM_N, len(real_pwds)))
        s_plus_seqs   = [encode_password(p) for p in s_plus_sample]
        s_plus_padded = pad_sequences(s_plus_seqs).to(device)
        s_plus_feat   = ranker.get_feature(s_plus_padded)   # (M, H)

        rewards = cosine_ranking_score(pw_feat, expert_feat,
                                       s_plus_feat=s_plus_feat)  # (N,)

    reward_std = rewards.std().item()
    if reward_std < cfg.REWARD_STD_MIN:
        print(f"    [警告] reward_std={reward_std:.2e} < {cfg.REWARD_STD_MIN}，"
              f"跳过 Guesser 更新 (Ranker 尚未收敛，属于正常早期现象)")
        ranker.train()
        return 0.0

    baseline = rewards.mean()
    adv      = rewards - baseline

    pg_loss = -(adv.detach() * seq_log_probs).mean()

    opt_guesser.zero_grad()
    pg_loss.backward()
    nn.utils.clip_grad_norm_(guesser.parameters(), 5.0)
    opt_guesser.step()

    ranker.train()
    return pg_loss.item()


# =============================================================================
# 10. Per-step MC rollout (可选，论文原版) — 【修复2: 加入 S⁺ 参数】
# =============================================================================
@torch.no_grad()
def mc_rollout_reward(guesser:     GuesserModel,
                       ranker:      RankerModel,
                       prefix_ids:  torch.Tensor,
                       prefix_h:    torch.Tensor,
                       expert_feat: torch.Tensor,
                       device,
                       s_plus_feat: torch.Tensor = None) -> torch.Tensor:
    """
    论文 Eq.6-7: 固定前缀后 N 次 rollout 的平均排名分。

    【修复2】新增 s_plus_feat 参数，传入 cosine_ranking_score 以正确
            归一化，与 Eq.5 保持一致。
    """
    B     = prefix_ids.size(0)
    q_sum = torch.zeros(B, device=device)

    for _ in range(cfg.MC_ROLLOUT_N):
        h    = prefix_h.clone()
        curr = prefix_ids[:, -1:]
        seqs = prefix_ids.tolist()
        done = [False] * B

        for _ in range(cfg.MAX_LEN + 1):
            logits, h = guesser.forward_step(curr, h)
            probs = F.softmax(logits, dim=-1)
            probs[:, cfg.SOS_IDX] = 0.0
            probs[:, cfg.PAD_IDX] = 0.0
            for i in range(B):
                if not done[i] and (len(seqs[i]) - 1) < cfg.MIN_LEN:
                    probs[i, cfg.EOS_IDX] = 0.0
            probs = probs / probs.sum(dim=-1, keepdim=True).clamp(min=1e-9)
            nxt = torch.multinomial(probs, 1)
            for i in range(B):
                if done[i]: continue
                tok = nxt[i, 0].item()
                seqs[i].append(tok)
                if tok == cfg.EOS_IDX or len(seqs[i]) > cfg.MAX_LEN + 2:
                    done[i] = True
            curr = nxt
            if all(done): break

        max_l  = max(len(s) for s in seqs)
        padded = torch.full((B, max_l), cfg.PAD_IDX,
                            dtype=torch.long, device=device)
        for i, s in enumerate(seqs):
            padded[i, :len(s)] = torch.tensor(s, dtype=torch.long)
        pw_feat = ranker.get_feature(padded)
        # 【修复2】传入 s_plus_feat
        q_sum  += cosine_ranking_score(pw_feat, expert_feat,
                                        s_plus_feat=s_plus_feat)

    return q_sum / cfg.MC_ROLLOUT_N


def train_guesser_per_step(guesser:     GuesserModel,
                            ranker:      RankerModel,
                            opt_guesser: optim.Optimizer,
                            train_pwds:  list,
                            expert_feat: torch.Tensor,
                            device,
                            n_traj:      int = 32) -> float:
    """
    原版 per-step MC rollout 策略梯度 (论文 Eq.6-8)，可通过
    --use_per_step_mc 启用。

    【修复4】删除无效的冗余 GRU 前向调用 (原第一行 "emb, _ = ...")。
    【修复2】预计算 S⁺ 特征并传入 mc_rollout_reward / cosine_ranking_score。
    """
    guesser.train()
    ranker.eval()

    # 【修复2】预计算 S⁺ 特征，在整个函数内复用
    s_plus_sample = random.sample(
        train_pwds, min(cfg.S_PLUS_DENOM_N, len(train_pwds)))
    s_plus_seqs   = [encode_password(p) for p in s_plus_sample]
    s_plus_padded = pad_sequences(s_plus_seqs).to(device)
    with torch.no_grad():
        s_plus_feat = ranker.get_feature(s_plus_padded)     # (M, H)

    sampled       = random.sample(train_pwds, min(n_traj, len(train_pwds)))
    log_prob_list = []
    reward_list   = []

    for pwd in sampled:
        full = encode_password(pwd)
        T    = len(full) - 1
        if T < 2: continue

        for t in range(1, T):
            prefix = full[:t + 1].unsqueeze(0).to(device)
            with torch.no_grad():
                # 【修复4】删除原先无效的第一行 GRU 调用
                #   原: emb, _ = guesser.gru(guesser.embedding(prefix))  ← 废行
                emb_full = guesser.embedding(prefix)
                _, h_pref = guesser.gru(emb_full)
            # 【修复2】传入 s_plus_feat
            delta_t = mc_rollout_reward(guesser, ranker, prefix,
                                         h_pref, expert_feat, device,
                                         s_plus_feat=s_plus_feat)
            inp_t  = prefix[:, :-1]
            logits = guesser(inp_t)
            log_pi = F.log_softmax(logits[:, -1, :], dim=-1)
            lp     = log_pi[0, full[t].item()]
            log_prob_list.append(lp)
            reward_list.append(delta_t.squeeze().detach())

    if not log_prob_list:
        ranker.train()
        return 0.0

    log_probs = torch.stack(log_prob_list)
    rewards   = torch.stack(reward_list)

    if rewards.std().item() < cfg.REWARD_STD_MIN:
        print(f"    [警告] per-step reward_std 过低，跳过 Guesser 更新")
        ranker.train()
        return 0.0

    baseline = rewards.mean()
    adv      = rewards - baseline
    pg_loss  = -(adv.detach() * log_probs).mean()
    opt_guesser.zero_grad()
    pg_loss.backward()
    nn.utils.clip_grad_norm_(guesser.parameters(), 5.0)
    opt_guesser.step()
    ranker.train()
    return pg_loss.item()


# =============================================================================
# 11. 评估
# =============================================================================
@torch.no_grad()
def evaluate_crack_rate(guesser: GuesserModel,
                         val_pwds: list,
                         device,
                         n_gen:    int = 3000) -> float:
    gen     = batch_generate(guesser, n_gen, device)
    val_set = set(val_pwds)
    matched = sum(1 for p in gen if p in val_set)
    rate    = matched / n_gen * 100.0
    print(f"  [破解率] {matched}/{n_gen} = {rate:.4f}%")
    return rate


@torch.no_grad()
def log_reward_stats(guesser:     GuesserModel,
                      ranker:      RankerModel,
                      expert_feat: torch.Tensor,
                      real_pwds:   list,
                      device,
                      n:           int = 200):
    """每轮打印奖励统计，包含 S⁺ 分母以与 Eq.5 对齐。"""
    ranker.eval()
    pwds   = batch_generate(guesser, n, device)
    seqs   = [encode_password(p) for p in pwds]
    padded = pad_sequences(seqs).to(device)
    feat   = ranker.get_feature(padded)

    # S⁺ 特征
    s_plus_sample = random.sample(
        real_pwds, min(cfg.S_PLUS_DENOM_N, len(real_pwds)))
    s_plus_seqs   = [encode_password(p) for p in s_plus_sample]
    s_plus_padded = pad_sequences(s_plus_seqs).to(device)
    s_plus_feat   = ranker.get_feature(s_plus_padded)

    scores = cosine_ranking_score(feat, expert_feat, s_plus_feat=s_plus_feat)
    print(f"  [奖励统计] mean={scores.mean():.4f}  "
          f"std={scores.std():.4f}  "
          f"max={scores.max():.4f}  "
          f"min={scores.min():.4f}")
    ranker.train()
    return scores.std().item()


# =============================================================================
# 12. 主训练入口
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="RankGuess Training (fixed v2)")
    parser.add_argument("--train_file",      type=str, default=cfg.TRAIN_FILE)
    parser.add_argument("--save_dir",        type=str, default=cfg.SAVE_DIR)
    parser.add_argument("--pretrain_epochs", type=int, default=cfg.PRETRAIN_EPOCHS)
    parser.add_argument("--total_epochs",    type=int, default=cfg.TOTAL_EPOCHS)
    parser.add_argument("--batch_size",      type=int, default=cfg.BATCH_SIZE)
    parser.add_argument("--seed",            type=int, default=42)
    parser.add_argument("--use_per_step_mc", action="store_true",
                        help="使用 per-step MC rollout (论文原版, 速度慢)")
    args = parser.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)

    os.makedirs(args.save_dir, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    print("=" * 60)
    print("RankGuess Training  (IEEE S&P 2025) — 修复版 v2")
    print("=" * 60)
    print(f"Device        : {device}")
    print(f"Vocab size    : {cfg.VOCAB_SIZE}")
    print(f"Hidden size   : {cfg.HIDDEN_SIZE}")
    print(f"Dropout       : {cfg.DROPOUT}     [论文: 0.3]")
    print(f"LR            : {cfg.LR}     [论文: 0.001]")
    print(f"Adam betas    : {cfg.ADAM_BETAS}  [论文: (0.5, 0.999)]")
    print(f"Penalty γ     : {cfg.PENALTY_GAMMA}     [论文: 0.1]")
    print(f"Pretrain ep   : {args.pretrain_epochs}         [论文: 5]")
    print(f"Total ep      : {args.total_epochs}        [论文: 20]")
    print(f"Group num     : {cfg.GROUP_NUM}        [论文: 17]")
    print(f"Expert top-k  : {cfg.TOP_EXPERT_K}   [论文: 1w]")
    print(f"S⁺ denom N    : {cfg.S_PLUS_DENOM_N}  [Eq.5 分母批次大小]")
    guesser_mode = "per-step MC rollout" if args.use_per_step_mc \
                   else "序列级 REINFORCE (推荐)"
    print(f"Guesser mode  : {guesser_mode}")
    print(f"S⁻ 来源      : Gθ 生成  [Algorithm 1 Line 3/8]")
    print("=" * 60)

    # ── 数据 ──
    print(f"\n[数据] 加载: {args.train_file}")
    all_pwds = load_passwords(args.train_file)
    print(f"  共 {len(all_pwds)} 条有效密码")
    random.shuffle(all_pwds)
    val_size   = int(len(all_pwds) * cfg.VAL_RATIO)
    val_pwds   = all_pwds[:val_size]
    train_pwds = all_pwds[val_size:]
    print(f"  训练: {len(train_pwds)}, 验证: {len(val_pwds)}")

    freq        = Counter(train_pwds)
    expert_pwds = [p for p, _ in freq.most_common(cfg.TOP_EXPERT_K)]
    print(f"  Expert 密码数: {len(expert_pwds)}")

    train_loader = DataLoader(
        PasswordDataset(train_pwds),
        batch_size=args.batch_size, shuffle=True,
        drop_last=False, num_workers=0)

    # ── 初始化模型 ──
    guesser = GuesserModel().to(device)
    ranker  = RankerModel().to(device)
    opt_g   = optim.Adam(guesser.parameters(),
                          lr=cfg.LR, betas=cfg.ADAM_BETAS)
    opt_r   = optim.Adam(ranker.parameters(),
                          lr=cfg.LR, betas=cfg.ADAM_BETAS)

    # ════════════════════════════════
    # Step 1a: Guesser MLE 预训练
    # ════════════════════════════════
    pretrain_guesser(guesser, train_loader, device, opt_g)

    # ════════════════════════════════
    # Step 1b: Ranker 预训练
    # ════════════════════════════════
    print(f"\n生成 S⁻ (n=20000) 用于 Ranker 预训练 ...")
    init_fake = batch_generate(guesser, 20000, device)
    pretrain_ranker(ranker, train_pwds, init_fake, device, opt_r)

    filename = os.path.basename(cfg.TRAIN_FILE)
    filename_without_ext = os.path.splitext(filename)[0]
    suffix = filename_without_ext.split("_")[-1]

    torch.save({"guesser_state_dict": guesser.state_dict(),
                "ranker_state_dict":  ranker.state_dict()},
               os.path.join(args.save_dir, f"pretrained_rankguess_{suffix}.pth"))
    print("预训练检查点已保存.")

    # ════════════════════════════════
    # 对抗训练
    # ════════════════════════════════
    print(f"\n[对抗训练] 共 {args.total_epochs} 轮 ...")
    best_crack = -1.0
    sched_g = optim.lr_scheduler.CosineAnnealingLR(opt_g, T_max=args.total_epochs)
    sched_r = optim.lr_scheduler.CosineAnnealingLR(opt_r, T_max=args.total_epochs)

    def compute_expert_feat():
        ranker.eval()
        with torch.no_grad():
            seqs  = [encode_password(p) for p in expert_pwds]
            feats = []
            for i in range(0, len(seqs), 256):
                b = pad_sequences(seqs[i:i + 256]).to(device)
                feats.append(ranker.get_feature(b))
        ranker.train()
        return torch.cat(feats, dim=0).mean(0)   # (H,)

    for epoch in range(1, args.total_epochs + 1):
        print(f"\n── Epoch {epoch}/{args.total_epochs} ──")

        # 每轮重新生成 S⁻ (当前 Gθ)
        fake_pwds   = batch_generate(guesser, 5000, device)
        expert_feat = compute_expert_feat()

        # ════════════════════════════════
        # 【修复3】Algorithm 1 的正确循环结构:
        #   先跑 K 次 Ranker 更新 (Line 7-11)
        #   再跑 K 次 Guesser 更新 (Line 14-17)
        # ════════════════════════════════

        # --- K 次 Ranker 更新 ---
        r_losses = []
        for k in range(cfg.K_ITER):
            r_loss = train_ranker_one_iter(
                ranker, opt_r, train_pwds, fake_pwds,
                expert_feat, device)
            r_losses.append(r_loss)

        # Ranker 更新完成后，刷新 expert_feat
        expert_feat = compute_expert_feat()

        # --- K 次 Guesser 更新 ---
        g_losses = []
        for k in range(cfg.K_ITER):
            if args.use_per_step_mc:
                g_loss = train_guesser_per_step(
                    guesser, ranker, opt_g,
                    train_pwds, expert_feat, device,
                    n_traj=32)
            else:
                g_loss = train_guesser_reinforce(
                    guesser, ranker, opt_g,
                    expert_feat, train_pwds, device,
                    n_samples=cfg.REINFORCE_N)
            g_losses.append(g_loss)

        sched_g.step()
        sched_r.step()

        print(f"  Ranker  loss: {np.mean(r_losses):.6f}")
        print(f"  Guesser loss: {np.mean(g_losses):.6f}")

        reward_std = log_reward_stats(
            guesser, ranker, expert_feat, train_pwds, device)

        if epoch % args.total_epochs == 0:
            crack = evaluate_crack_rate(guesser, val_pwds, device)
            if crack > best_crack:
                best_crack = crack
                torch.save({"model_state_dict": guesser.state_dict(),
                            "epoch": epoch, "crack_rate": crack},
                           os.path.join(args.save_dir,
                                        f"best_rankguess_guesser_{suffix}.pth"))
                torch.save({"model_state_dict": ranker.state_dict()},
                           os.path.join(args.save_dir,
                                        f"best_rankguess_ranker_{suffix}.pth"))
                print(f"  ✓ 最优模型已保存 (crack={crack:.4f}%)")

    print("\n[生成示例]")
    for p in batch_generate(guesser, 10, device):
        print(f"  {p}")


if __name__ == "__main__":
    main()