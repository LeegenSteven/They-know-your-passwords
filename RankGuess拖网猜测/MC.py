import os
import re
import argparse
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
import numpy as np
import matplotlib
matplotlib.use("Agg")
import torch
import torch.nn as nn
import torch.nn.functional as F
from matplotlib import pyplot as plt
from tqdm import tqdm

# ===================== 配置 =====================
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
    VOCAB_SIZE  = len(CHARS)
    SOS_IDX     = CHAR2IDX[SOS_TOKEN]
    EOS_IDX     = CHAR2IDX[EOS_TOKEN]
    PAD_IDX     = CHAR2IDX[PAD_TOKEN]
    MIN_LEN     = 5
    MAX_LEN     = 20
    EMBED_DIM   = 64
    HIDDEN_SIZE = 256
    NUM_LAYERS  = 3
    DROPOUT     = 0.3

cfg = Config()

# ===================== 模型定义 =====================
class GuesserModel(nn.Module):
    def __init__(self):
        super().__init__()
        self.vocab_size  = cfg.VOCAB_SIZE
        self.hidden_size = cfg.HIDDEN_SIZE
        self.num_layers  = cfg.NUM_LAYERS
        self.embed_dim   = cfg.EMBED_DIM
        self.embedding  = nn.Embedding(cfg.VOCAB_SIZE, cfg.EMBED_DIM, padding_idx=cfg.PAD_IDX)
        self.gru = nn.GRU(input_size=cfg.EMBED_DIM, hidden_size=cfg.HIDDEN_SIZE, num_layers=cfg.NUM_LAYERS, batch_first=True, dropout=cfg.DROPOUT if cfg.NUM_LAYERS > 1 else 0.0)
        self.layer_norm = nn.LayerNorm(cfg.HIDDEN_SIZE)
        self.fc1        = nn.Linear(cfg.HIDDEN_SIZE, cfg.HIDDEN_SIZE)
        self.fc2        = nn.Linear(cfg.HIDDEN_SIZE, cfg.VOCAB_SIZE)
        self.activation = nn.GELU()
        self.dropout    = nn.Dropout(cfg.DROPOUT)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        emb      = self.embedding(x)
        out, _   = self.gru(emb)
        out      = self.layer_norm(out)
        residual = out
        out      = self.activation(self.fc1(out))
        out      = self.dropout(out)
        out      = out + residual
        return self.fc2(out)

    def forward_step(self, x_t: torch.Tensor, h: torch.Tensor):
        emb      = self.embedding(x_t)
        out, h   = self.gru(emb, h)
        out      = self.layer_norm(out)
        residual = out
        out      = self.activation(self.fc1(out))
        out      = out + residual
        logits   = self.fc2(out[:, 0, :])
        return logits, h

# ===================== 模型加载 =====================
def load_model(model_path: str, device) -> GuesserModel:
    print(f"[加载模型] {model_path}")
    model = GuesserModel().to(device)
    ckpt  = torch.load(model_path, map_location=device, weights_only=True)
    if "model_state_dict" in ckpt:
        model.load_state_dict(ckpt["model_state_dict"])
    elif "guesser_state_dict" in ckpt:
        model.load_state_dict(ckpt["guesser_state_dict"])
    else:
        model.load_state_dict(ckpt)
    model.eval()
    return model

# ===================== 编码密码 =====================
def encode_password(pwd: str) -> torch.Tensor:
    idxs = ([cfg.SOS_IDX] + [cfg.CHAR2IDX[c] for c in pwd if c in cfg.CHAR2IDX] + [cfg.EOS_IDX])
    return torch.tensor(idxs, dtype=torch.long)

# ===================== 批量概率计算 =====================
def calculate_probability_batch(model: GuesserModel, passwords: list, device, batch_size: int = 256) -> list:
    model.eval()
    results = []
    encoded_data = []
    for pwd in passwords:
        if not (cfg.MIN_LEN <= len(pwd) <= cfg.MAX_LEN):
            continue
        if not all(c in cfg.CHAR2IDX for c in pwd):
            continue
        encoded_data.append((pwd, encode_password(pwd)))

    with torch.no_grad():
        for i in range(0, len(encoded_data), batch_size):
            batch   = encoded_data[i:i + batch_size]
            pwds    = [x[0] for x in batch]
            tensors = [x[1] for x in batch]
            max_len = max(t.size(0) for t in tensors)
            padded  = torch.full((len(tensors), max_len), cfg.PAD_IDX, dtype=torch.long)
            for j, t in enumerate(tensors):
                padded[j, :t.size(0)] = t
            padded = padded.to(device)
            inp = padded[:, :-1]
            tgt = padded[:, 1:]
            logits    = model(inp)
            log_probs = F.log_softmax(logits, dim=-1)
            tgt_log_p = log_probs.gather(2, tgt.unsqueeze(-1)).squeeze(-1)
            mask      = (tgt != cfg.PAD_IDX).float()
            sum_logp  = (tgt_log_p * mask).sum(dim=1)
            probs     = torch.exp(sum_logp).cpu().numpy()
            for j, p in enumerate(probs):
                results.append((pwds[j], float(p)))
    return results

# ===================== Monte Carlo 采样 =====================
def monte_carlo_estimation(model: GuesserModel, device, sample_size: int = 100000, batch_size: int = 1000) -> tuple:
    generated_log_probs = []
    n_batches = sample_size // batch_size
    model.eval()
    with torch.no_grad():
        for _ in tqdm(range(n_batches), desc="  MC 采样"):
            B = batch_size
            h    = torch.zeros(model.num_layers, B, model.hidden_size, device=device)
            curr = torch.full((B, 1), cfg.SOS_IDX, dtype=torch.long, device=device)
            step_log_probs = []
            finished       = torch.zeros(B, dtype=torch.bool, device=device)
            for step in range(cfg.MAX_LEN + 1):
                logits, h = model.forward_step(curr, h)
                probs     = F.softmax(logits, dim=-1)
                probs[:, cfg.SOS_IDX] = 0.0
                probs[:, cfg.PAD_IDX] = 0.0
                if step < cfg.MIN_LEN:
                    probs[:, cfg.EOS_IDX] = 0.0
                probs = probs / probs.sum(dim=-1, keepdim=True).clamp(min=1e-9)
                next_tok = torch.multinomial(probs, 1)
                sel_prob = probs.gather(1, next_tok).squeeze(1)
                cur_prob = sel_prob.clone()
                cur_prob[finished] = 1.0
                step_log_probs.append(torch.log(cur_prob + 1e-45))
                finished = finished | (next_tok.squeeze(1) == cfg.EOS_IDX)
                curr = next_tok
                if finished.all():
                    break
            total_log_p = torch.stack(step_log_probs, dim=1).sum(dim=1)
            generated_log_probs.extend(total_log_p.cpu().numpy().tolist())
    log_probs = np.array(generated_log_probs)
    valid     = np.isfinite(log_probs) & (log_probs > -1000)
    log_probs = log_probs[valid]
    probs = np.exp(log_probs)
    order = np.argsort(-probs)
    probs_desc = probs[order]
    N = len(probs_desc)
    weights = 1.0 / np.maximum(probs_desc, 1e-300)
    cum_weights = np.cumsum(weights)
    ref_guesses = cum_weights / N
    return probs_desc, ref_guesses

# ===================== 查表 =====================
def get_guess_number(prob: float, ref_probs: np.ndarray, ref_guesses: np.ndarray) -> float:
    if prob <= 0 or not np.isfinite(prob):
        return float("inf")
    idx = np.searchsorted(-ref_probs, -prob)
    if idx == 0:
        return 1.0
    elif idx >= len(ref_guesses):
        return float(ref_guesses[-1])
    else:
        return float(ref_guesses[idx - 1])


# ===================== 结果保存工具 =====================
def save_mc_curve_png(ref_probs: np.ndarray, ref_guesses: np.ndarray, output_dir: str, exp_tag: str) -> str:
    """将 Monte Carlo 参考曲线保存为 PNG 图片。"""
    png_path = os.path.join(output_dir, f"mc_curve_{exp_tag}.png")

    plt.figure(figsize=(10, 6))
    plt.plot(ref_guesses, ref_probs, label="Monte Carlo Estimation", linewidth=2)
    plt.title(f"Guessing Success Probability ({exp_tag})")
    plt.xlabel("Number of Guesses")
    plt.ylabel("Success Probability")
    plt.xscale("log")
    plt.grid(True, linestyle="--", alpha=0.7)
    plt.legend()
    plt.tight_layout()
    plt.savefig(png_path, dpi=300, format="png")
    plt.close()

    print(f"MC 曲线图已保存: {png_path}")
    return png_path


def save_guess_effect_txt(guess_results: list, output_dir: str, exp_tag: str) -> str:
    """
    单独保存 10^1 到 10^25 猜测规模下的破解数量。
    破解数量定义为: Est_Guesses <= 10^k 的测试集密码数量。
    """
    effect_file = os.path.join(output_dir, f"guess_effect_{exp_tag}.txt")
    total = len(guess_results)

    with open(effect_file, "w", encoding="utf-8") as f:
        f.write("Guess_Power\tGuess_Threshold\tCracked_Count\tTotal_Valid_Test\tCrack_Rate\n")
        res = []
        for k in range(1, 26):
            threshold = 10.0 ** k
            cracked = sum(
                1 for item in guess_results
                if np.isfinite(item["guess_number"]) and item["guess_number"] <= threshold
            )
            rate = cracked / total * 100.0 if total > 0 else 0.0
            res.append(cracked)
            f.write(f"10^{k}\t{threshold:.0e}\t{cracked}\t{total}\t{rate:.6f}%\n")
        f.write(str(res))
    print(f"猜测效果统计已保存: {effect_file}")
    return effect_file

# ===================== 文件名工具 =====================
def safe_stem(path: str) -> str:
    """
    从 Linux/Windows 路径里取文件主名，并清理成适合保存结果文件的标签。
    例如: D:\\data\\train.txt -> train
    """
    if path is None:
        return "unknown"
    name = re.split(r"[\\/]", str(path).strip())[-1]
    stem = os.path.splitext(name)[0] or "unknown"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return stem or "unknown"


def build_experiment_tag(model_path: str, test_file: str, train_file: str = None) -> str:
    """
    生成不会互相覆盖的实验标签。
    有 train_file 时: train_<train>__model_<model>__test_<test>
    无 train_file 时: model_<model>__test_<test>
    """
    model_name = safe_stem(model_path)
    test_name = safe_stem(test_file)
    if train_file:
        train_name = safe_stem(train_file)
        return f"train_{train_name}__model_{model_name}__test_{test_name}"
    return f"model_{model_name}__test_{test_name}"

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


# ===================== 主程序 =====================
def main():
    # ===================== 手动配置区 =====================
    sample_size = 100000
    batch_size = 1000
    output_dir = "results"

    # 测试集所在目录，根据你的实际路径修改这里即可
    test_root = convert_windows_path(r"D:\研究生\Password_Dataset\Password_Dataset")

    model_paths = [
        "save/best_rankguess_guesser_000webhost.pth",
        "save/best_rankguess_guesser_7k7k.pth",
        "save/best_rankguess_guesser_clixsense.pth",
        "save/best_rankguess_guesser_craftrise.pth",
        "save/best_rankguess_guesser_csdn.pth",
        "save/best_rankguess_guesser_dodonew.pth",
        "save/best_rankguess_guesser_gmail.pth",
        "save/best_rankguess_guesser_linkedin.pth",
        "save/best_rankguess_guesser_mathway.pth",
        "save/best_rankguess_guesser_neopets.pth",
        "save/best_rankguess_guesser_netease.pth",
        "save/best_rankguess_guesser_parkmobile.pth",
        "save/best_rankguess_guesser_rockyou.pth",
        "save/best_rankguess_guesser_taobao.pth",
        "save/best_rankguess_guesser_tianya.pth"
    ]

    test_paths = [
        "testword_000webhost.txt",
        "testword_7k7k.txt",
        "testword_clixsense.txt",
        "testword_craftrise.txt",
        "testword_csdn.txt",
        "testword_dodonew.txt",
        "testword_gmail.txt",
        "testword_linkedin.txt",
        "testword_mathway.txt",
        "testword_neopets.txt",
        "testword_netease.txt",
        "testword_parkmobile.txt",
        "testword_rockyou.txt",
        "testword_taobao.txt",
        "testword_tianya.txt"
    ]

    # ===================== 初始化 =====================
    os.makedirs(output_dir, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[设备] {device}")
    print(f"[模型数量] {len(model_paths)}")
    print(f"[测试集数量] {len(test_paths)}")
    print(f"[总实验数] {len(model_paths) * len(test_paths)}")

    # ===================== 双层循环实验 =====================
    for model_idx, model_path in enumerate(model_paths, start=1):
        print("\n" + "=" * 80)
        print(f"[模型 {model_idx}/{len(model_paths)}] {model_path}")
        print("=" * 80)

        if not os.path.exists(model_path):
            print(f"[跳过] 模型文件不存在: {model_path}")
            continue

        # 每个模型只加载一次
        model = load_model(model_path, device)

        # 每个模型只做一次 MC 采样
        print(f"[MC 估计] sample_size={sample_size}, batch_size={batch_size}")
        ref_probs, ref_guesses = monte_carlo_estimation(
            model,
            device,
            sample_size=sample_size,
            batch_size=batch_size
        )

        # 保存该模型对应的 MC 曲线 PNG
        model_tag = safe_stem(model_path)
        save_mc_curve_png(ref_probs, ref_guesses, output_dir, model_tag)

        # 内层循环：当前模型依次测试所有测试集
        for test_idx, test_name in enumerate(test_paths, start=1):
            test_file = os.path.join(test_root, test_name)
            test_file = convert_windows_path(test_file)

            print("\n" + "-" * 80)
            print(f"[测试 {test_idx}/{len(test_paths)}]")
            print(f"模型: {model_path}")
            print(f"测试集: {test_file}")
            print("-" * 80)

            if not os.path.exists(test_file):
                print(f"[跳过] 测试集文件不存在: {test_file}")
                continue

            exp_tag = build_experiment_tag(
                model_path=model_path,
                test_file=test_file,
                train_file=None
            )

            # 读取测试集
            with open(test_file, "r", encoding="latin-1", errors="ignore") as f:
                test_pwds = [line.strip() for line in f if line.strip()]

            print(f"[测试集] 共读取 {len(test_pwds)} 条密码")

            # 计算测试集密码概率
            test_results = calculate_probability_batch(
                model,
                test_pwds,
                device,
                batch_size=256
            )

            print(f"[有效测试密码] {len(test_results)} 条")

            # 查表得到猜测数
            guess_results = []
            for pwd, prob in tqdm(test_results, desc="查表"):
                gn = get_guess_number(prob, ref_probs, ref_guesses)
                guess_results.append({
                    "pwd": pwd,
                    "prob": prob,
                    "guess_number": gn
                })

            guess_results.sort(key=lambda x: x["guess_number"])

            # 保存详细结果
            result_file = os.path.join(output_dir, f"detail_results_{exp_tag}.txt")
            with open(result_file, "w", encoding="utf-8") as f:
                f.write("Password\tProbability\tEst_Guesses\n")
                for item in guess_results:
                    gn_str = (
                        f"{item['guess_number']:.2e}"
                        if item["guess_number"] != float("inf")
                        else "inf"
                    )
                    f.write(f"{item['pwd']}\t{item['prob']:.6e}\t{gn_str}\n")

            print(f"[保存] 详细结果已保存: {result_file}")

            # 保存 10^1 到 10^25 猜测规模下的破解数量
            save_guess_effect_txt(guess_results, output_dir, exp_tag)

        # 释放当前模型显存，进入下一个模型
        del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    print("\n" + "=" * 80)
    print("[完成] 所有模型 × 测试集实验已运行结束")
    print("=" * 80)

if __name__ == "__main__":
    np.random.seed(42)
    torch.manual_seed(42)
    main()
