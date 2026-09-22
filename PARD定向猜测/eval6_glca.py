"""Evaluate checkpoints produced by demo6_glca.py.

Data parsing and vectorized beam search are implemented in this file. Only
the source-side student and shared decoder run at inference. The GLCA teacher
is reconstructed solely so that the complete checkpoint can be loaded strictly;
target passwords are used only for offline hit-rate calculation.
"""

import argparse
import csv
import json
import os
import sys
import time
from typing import List, Optional, Tuple

import torch
import torch.nn.functional as F
from torch_geometric.data import Batch

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from demo6_glca import (
    GLCA_ARCHITECTURE,
    DEFAULT_CROSS_LATENT_DIM,
    Pass2EditGLCA,
    build_graph,
    load_checkpoint,
    is_valid_password,
    password_to_indices,
    decode_tgt,
    TGT_PAD,
    TGT_BOS,
    TGT_EOS,
    EMBED_DIM,
    HGT_HIDDEN_DIM,
    NUM_HGT_LAYERS,
    NUM_DEC_LAYERS,
    NUM_HEADS,
    FC_HIDDEN_DIM,
    DROPOUT,
    LATENT_DIM,
    MAX_TGT_LEN,
    MEMORY_LEN,
    TGT_VOCAB_SIZE,
)


BEAM_WIDTH = 1000
MAX_DECODE_DEPTH = MAX_TGT_LEN
TOPK_THRESHOLDS = [1, 10, 100, 1000]
DEFAULT_EVAL_BATCH = 24

def _load_checkpoint_config(checkpoint_path: str) -> dict:
    checkpoint = torch.load(checkpoint_path, map_location="cpu")
    if "model_state" not in checkpoint:
        raise ValueError(f"无效 checkpoint（缺少 model_state）: {checkpoint_path}")
    config = checkpoint.get("model_config", {}) or {}
    architecture = config.get("architecture")
    if architecture != GLCA_ARCHITECTURE:
        raise ValueError(
            "eval6_glca.py 只能加载 GLCA checkpoint；"
            f"当前 architecture={architecture!r}。"
            "请使用由 GLCA 训练脚本生成的模型。"
        )
    return config


def build_model_from_config(config: dict) -> Pass2EditGLCA:
    """Reconstruct every shape-affecting option before strict state loading."""
    return Pass2EditGLCA(
        embed_dim=int(config.get("embed_dim", EMBED_DIM)),
        hgt_hidden=int(config.get("hidden_dim", HGT_HIDDEN_DIM)),
        num_enc_layers=int(config.get("teacher_num_layers", NUM_HGT_LAYERS)),
        num_dec_layers=int(config.get("num_dec_layers", NUM_DEC_LAYERS)),
        num_heads=int(config.get("num_heads", NUM_HEADS)),
        fc_dim=int(config.get("fc_dim", FC_HIDDEN_DIM)),
        dropout=float(config.get("dropout", DROPOUT)),
        latent_dim=int(config.get("latent_dim", LATENT_DIM)),
        cross_latent_dim=int(
            config.get("cross_latent_dim", DEFAULT_CROSS_LATENT_DIM)
        ),
        use_view_embed=bool(config.get("use_view_embed", True)),
    )


def fast_batched_beam_search(
    model:         Pass2EditGLCA,
    src_passwords: List[str],
    device:        torch.device,
    beam_width:    int  = BEAM_WIDTH,
    max_depth:     int  = MAX_DECODE_DEPTH,
    top_n:         int  = 1000,
    use_amp:       bool = True,
    use_seq_edge:   bool = True,
    use_align_edge: bool = True,
    use_match_edge: bool = True,
) -> List[List[Tuple[str, float]]]:
    """
    向量化并行 Beam Search。

    编码：仅使用 Source Student。GLCA Teacher 不参与推理。
    Memory：(n_pw, MEMORY_LEN=20, H)，S 动态读取

    加速项：
      ① seqs 预分配 Tensor，消除每步 CPU 循环构建开销
      ② beam expansion 完全向量化
      ③ autocast FP16 推理（GPU 约 1.5-2x 提速）
      ④ early exit：全部 beam 已 EOS 的口令提前退出

    Returns:
        List[List[(pw, log_prob)]]，与 src_passwords 等长，降序，最多 top_n 条
    """
    model.eval()
    n_pw   = len(src_passwords)
    V      = TGT_VOCAB_SIZE                    # 98
    k_eff  = min(beam_width, V)                # 词表上限裁剪
    BW     = beam_width
    amp_ok = use_amp and (device.type == 'cuda')

    # ── 预分配 beam state tensor ─────────────────────────────────────────
    seqs   = torch.full((n_pw, BW, max_depth + 1), TGT_PAD, dtype=torch.long,  device=device)
    scores = torch.full((n_pw, BW),                float('-inf'),               device=device)
    alive  = torch.zeros((n_pw, BW),               dtype=torch.bool,           device=device)

    valid_pw = [is_valid_password(s) for s in src_passwords]
    for i, v in enumerate(valid_pw):
        if v:
            seqs[i, 0, 0] = TGT_BOS
            scores[i, 0]  = 0.0
            alive[i, 0]   = True

    finished: List[List[Tuple[float, str]]] = [[] for _ in range(n_pw)]

    # Compatibility graphs contain two source copies, but model.encode() is
    # explicitly the source-only student path and reads only the orig nodes.
    graphs = [
        build_graph(
            password_to_indices(s), password_to_indices(s),
            use_seq=use_seq_edge,
            use_align=use_align_edge,
            use_match=use_match_edge,
        )
        if v else build_graph([0], [0], use_seq=use_seq_edge, use_align=use_align_edge, use_match=use_match_edge)
        for s, v in zip(src_passwords, valid_pw)
    ]
    batch_enc = Batch.from_data_list(graphs).to(device)
    with torch.no_grad():
        with torch.autocast(device_type=device.type, enabled=amp_ok, dtype=torch.float16):
            memory, mem_pad = model.encode(batch_enc)   # (n_pw, MEMORY_LEN, H)
    memory = memory.float()

    # S 动态读取，无论 MEMORY_LEN 是 20 还是其他值均自动适配
    S, H = memory.size(1), memory.size(2)

    # ── 逐步解码 ───────────────────────────────────────────────────────
    for step in range(1, max_depth + 1):

        pw_alive_mask = alive.any(dim=1)
        if not pw_alive_mask.any():
            break
        live_idx = pw_alive_mask.nonzero(as_tuple=True)[0]
        n_live   = live_idx.size(0)

        tgt_flat = seqs[live_idx, :, :step].reshape(n_live * BW, step)

        mem_live = memory[live_idx]
        pad_live = mem_pad[live_idx]
        mem_exp  = mem_live.unsqueeze(1).expand(-1, BW, -1, -1).reshape(n_live * BW, S, H)
        pad_exp  = pad_live.unsqueeze(1).expand(-1, BW, -1 ).reshape(n_live * BW, S)

        with torch.no_grad():
            with torch.autocast(device_type=device.type, enabled=amp_ok, dtype=torch.float16):
                logits = model.decode(tgt_flat, mem_exp, pad_exp)
            last_lp = F.log_softmax(logits[:, -1, :].float(), dim=-1)

        last_lp = last_lp.view(n_live, BW, V)

        top_lp, top_tok = last_lp.topk(k_eff, dim=-1)

        cur_scores   = scores[live_idx]
        new_scores_3 = cur_scores.unsqueeze(-1) + top_lp
        new_scores_3 = torch.nan_to_num(new_scores_3,
                                        nan=float('-inf'),
                                        neginf=float('-inf'))

        is_eos = (top_tok == TGT_EOS)

        seqs_cpu_step = seqs[live_idx, :, :step].cpu()
        eos_scores_2d = new_scores_3.masked_fill(~is_eos, float('-inf')).view(n_live, BW * k_eff)
        top_tok_2d    = top_tok.view(n_live, BW * k_eff)

        for i, pw_i in enumerate(live_idx.tolist()):
            eos_pos = (top_tok_2d[i] == TGT_EOS).nonzero(as_tuple=True)[0]
            for flat_idx in eos_pos.tolist():
                score = eos_scores_2d[i, flat_idx].item()
                if score == float('-inf'):
                    continue
                b  = flat_idx // k_eff
                pw = decode_tgt(seqs_cpu_step[i, b, :].tolist()[1:])
                if pw and is_valid_password(pw):
                    finished[pw_i].append((score, pw))

        non_eos_scores = new_scores_3.masked_fill(is_eos, float('-inf'))
        non_eos_2d     = non_eos_scores.view(n_live, BW * k_eff)

        sel_scores, sel_flat = non_eos_2d.topk(BW, dim=1)
        sel_b = sel_flat // k_eff
        sel_t = sel_flat % k_eff

        row     = torch.arange(n_live, device=device).unsqueeze(1).expand(-1, BW)
        sel_tok = top_tok[row, sel_b, sel_t]

        old_seqs             = seqs[live_idx]
        new_seqs             = old_seqs[row, sel_b, :]
        new_seqs[:, :, step] = sel_tok
        seqs[live_idx]       = new_seqs

        scores[live_idx] = sel_scores
        alive[live_idx]  = sel_scores.isfinite()

    # ── 收集仍活跃的假设 ─────────────────────────────────────────────────
    seqs_cpu = seqs.cpu()
    for pw_i in range(n_pw):
        for b in range(BW):
            if not alive[pw_i, b]:
                continue
            pw = decode_tgt(seqs_cpu[pw_i, b, :].tolist()[1:])
            s  = scores[pw_i, b].item()
            if pw and is_valid_password(pw):
                finished[pw_i].append((s, pw))

    # ── 去重排序，截取 top_n ─────────────────────────────────────────────
    results = []
    for pw_i in range(n_pw):
        seen, cands = set(), []
        for score, pw in sorted(finished[pw_i], reverse=True):
            if pw not in seen:
                seen.add(pw)
                cands.append((pw, score))
            if len(cands) >= top_n:
                break
        results.append(cands)

    return results


def load_pairs(
        data_path: str,
        max_pairs: Optional[int] = None,
) -> List[Tuple[str, str]]:
    """Load ``(src, tgt)`` pairs from JSONL or CSV without corrupting JSONL.

    Supported records:
      * ``{"src": "...", "tgt": "..."}``
      * ``["src", "tgt"]``
      * ordinary two-column CSV
      * a JSON array stored in the first CSV cell
    """
    pairs: List[Tuple[str, str]] = []

    def append_pair(src, tgt) -> bool:
        if src is None or tgt is None:
            return False
        src, tgt = str(src), str(tgt)
        if is_valid_password(src) and is_valid_password(tgt):
            pairs.append((src, tgt))
        return bool(max_pairs and len(pairs) >= max_pairs)

    suffix = os.path.splitext(data_path)[1].lower()
    with open(data_path, 'r', encoding='utf-8-sig', errors='ignore', newline='') as f:
        if suffix == '.csv':
            # gmail_test.csv stores one CSV-escaped JSON array in each row:
            #   "[""jinny"", ""angeline""]"
            # csv.reader first restores it to: ["jinny", "angeline"]
            for row in csv.reader(f):
                if not row:
                    continue
                src = tgt = None
                cell = row[0].strip()
                if len(row) == 1 and cell.startswith('['):
                    try:
                        record = json.loads(cell)
                        if isinstance(record, list) and len(record) >= 2:
                            src, tgt = record[0], record[1]
                    except json.JSONDecodeError:
                        continue
                elif len(row) >= 2:
                    # Also accept conventional two-column CSV files.
                    src, tgt = row[0], row[1]
                if append_pair(src, tgt):
                    break
        else:
            # All other current test sets are JSONL dictionaries.
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    src, tgt = record.get('src'), record.get('tgt')
                elif isinstance(record, list) and len(record) >= 2:
                    src, tgt = record[0], record[1]
                else:
                    continue
                if append_pair(src, tgt):
                    break
    return pairs


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance，用于论文分析生成候选是否只是复制 src。"""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(
                prev[j] + 1,
                cur[j - 1] + 1,
                prev[j - 1] + (ca != cb),
            ))
        prev = cur
    return prev[-1]


def fmt_pct(num: int, denom: int) -> str:
    if denom == 0:
        return "N/A"
    return f"{num / denom * 100:.2f}%"


def _print_progress(done: int, total: int, cracked: dict, elapsed: float):
    bar_len = 28
    filled  = int(bar_len * done / max(total, 1))
    bar     = '█' * filled + '░' * (bar_len - filled)
    eta     = (elapsed / done) * (total - done) if done > 0 else 0
    spd     = done / elapsed if elapsed > 0 else 0
    stats   = "  ".join(
        f"@{k}: {cracked[k]}({fmt_pct(cracked[k], done)})"
        for k in TOPK_THRESHOLDS
    )
    print(
        f"\r[{bar}] {done:>5}/{total}  "
        f"{spd:>5.1f}pw/s  ETA {eta:>5.0f}s  |  {stats}",
        end='', flush=True,
    )


def evaluate(
    model: Pass2EditGLCA,
    pairs: List[Tuple[str, str]],
    device: torch.device,
    beam_width: int = BEAM_WIDTH,
    max_depth: int = MAX_DECODE_DEPTH,
    eval_batch_size: int = DEFAULT_EVAL_BATCH,
    use_amp: bool = True,
    verbose: bool = True,
) -> dict:
    """Run the original vectorized beam search with GLCA-specific reporting."""
    model.eval()
    total = len(pairs)
    max_k = max(TOPK_THRESHOLDS)
    cracked = {k: 0 for k in TOPK_THRESHOLDS}
    details = []
    copy_top1 = 0
    valid_top1 = 0
    sum_top1_src_ed = 0
    sum_tgt_src_ed = 0
    start_time = time.time()

    amp_note = (
        "FP16 autocast 已启用"
        if use_amp and device.type == "cuda"
        else "FP32"
    )
    if verbose:
        print(f"\n{'=' * 70}")
        print(
            f"  评估集: {total:,} 条    Beam Width: {beam_width}"
            f"    Max Depth: {max_depth}    并发: {eval_batch_size} pw/批"
        )
        print(
            f"  解码: 字符级向量化 Beam Search（词表 {TGT_VOCAB_SIZE} 维）  "
            f"{amp_note}"
        )
        print(f"  Memory: (B, {MEMORY_LEN}, H)  设备: {device}")
        print("  Inference path: source-only Student (GLCA Teacher disabled)")
        print(f"{'=' * 70}\n")

    completed = 0
    for batch_start in range(0, total, eval_batch_size):
        batch_pairs = pairs[batch_start : batch_start + eval_batch_size]
        sources = [source for source, _ in batch_pairs]
        candidates_per_source = fast_batched_beam_search(
            model,
            sources,
            device,
            beam_width=beam_width,
            max_depth=max_depth,
            top_n=max_k,
            use_amp=use_amp,
            use_seq_edge=False,
            use_align_edge=False,
            use_match_edge=False,
        )

        for (source, target), candidates in zip(
            batch_pairs, candidates_per_source
        ):
            rank = None
            for candidate_rank, (password, _) in enumerate(candidates, 1):
                if password == target:
                    rank = candidate_rank
                    break

            for threshold in TOPK_THRESHOLDS:
                if rank is not None and rank <= threshold:
                    cracked[threshold] += 1

            top1_password = candidates[0][0] if candidates else None
            top1_score = candidates[0][1] if candidates else None
            target_source_ed = edit_distance(source, target)
            top1_source_ed = (
                edit_distance(source, top1_password)
                if top1_password is not None
                else None
            )
            sum_tgt_src_ed += target_source_ed
            if top1_password is not None:
                valid_top1 += 1
                sum_top1_src_ed += top1_source_ed
                if top1_password == source:
                    copy_top1 += 1

            details.append(
                {
                    "src": source,
                    "tgt": target,
                    "rank": rank,
                    "guesses_tried": len(candidates),
                    "top1": top1_password,
                    "top1_score": top1_score,
                    "top1_equals_src": (
                        bool(top1_password == source)
                        if top1_password is not None
                        else None
                    ),
                    "edit_distance_src_tgt": target_source_ed,
                    "edit_distance_src_top1": top1_source_ed,
                }
            )

        completed += len(batch_pairs)
        if verbose:
            _print_progress(
                completed,
                total,
                cracked,
                time.time() - start_time,
            )

    elapsed = time.time() - start_time
    if verbose:
        print()
        print(f"\n{'=' * 70}")
        print(
            f"  评估完成  {total:,} 条  用时 {elapsed:.1f}s  "
            f"({elapsed / max(total, 1) * 1000:.1f} ms/条)"
        )
        print(f"{'=' * 70}")
        print(f"  {'猜测次数':^10} {'破解数':^10} {'破解率':^14}")
        print(f"  {'-' * 38}")
        for threshold in TOPK_THRESHOLDS:
            print(
                f"  Top-{threshold:<6} {cracked[threshold]:^10,} "
                f"{fmt_pct(cracked[threshold], total):^14}"
            )
        print(
            "  Top-1 copy rate: "
            f"{copy_top1 / max(valid_top1, 1) * 100:.2f}%"
        )
        print(
            "  Avg ED(src,tgt): "
            f"{sum_tgt_src_ed / max(total, 1):.3f}"
        )
        print(
            "  Avg ED(src,top1): "
            f"{sum_top1_src_ed / max(valid_top1, 1):.3f}"
        )
        print(f"{'=' * 70}\n")

    return {
        "total": total,
        "cracked": cracked,
        "crack_rate": {
            k: cracked[k] / max(total, 1) for k in TOPK_THRESHOLDS
        },
        "details": details,
        "elapsed_sec": elapsed,
        "analysis": {
            "top1_copy_rate": copy_top1 / max(valid_top1, 1),
            "avg_edit_distance_src_tgt": sum_tgt_src_ed / max(total, 1),
            "avg_edit_distance_src_top1": (
                sum_top1_src_ed / max(valid_top1, 1)
            ),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "GLCA Teacher two-stage model evaluation "
            "(source-only Student inference)"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--data", default=os.path.join(SCRIPT_DIR, "data", "gmail_test.csv")
    )
    parser.add_argument("--beam-width", type=int, default=BEAM_WIDTH)
    parser.add_argument("--max-depth", type=int, default=MAX_DECODE_DEPTH)
    parser.add_argument("--max-pairs", type=int, default=None)
    parser.add_argument(
        "--eval-batch-size", type=int, default=DEFAULT_EVAL_BATCH
    )
    parser.add_argument("--no-amp", action="store_true")
    parser.add_argument("--device", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--quiet", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    device = torch.device(
        args.device
        if args.device
        else ("cuda" if torch.cuda.is_available() else "cpu")
    )

    print(f"[加载 GLCA 模型] {args.model}  ->  {device}")
    checkpoint_config = _load_checkpoint_config(args.model)
    print(f"[checkpoint config] {checkpoint_config}")
    model = load_checkpoint(
        build_model_from_config(checkpoint_config), args.model, device
    )
    model.eval()

    print(f"[加载数据] {args.data}")
    pairs = load_pairs(args.data, max_pairs=args.max_pairs)
    print(f"  有效口令对: {len(pairs):,}")
    if not pairs:
        raise ValueError("未加载到有效口令对，请检查数据文件")

    result = evaluate(
        model,
        pairs,
        device,
        beam_width=args.beam_width,
        max_depth=args.max_depth,
        eval_batch_size=args.eval_batch_size,
        use_amp=not args.no_amp,
        verbose=not args.quiet,
    )

    if args.output and args.output != "None":
        save_object = {
            "model": args.model,
            "data": args.data,
            "model_version": "GLCA-Teacher-SourceStudent-TwoStage",
            "inference_path": "source_student_only",
            "model_config": checkpoint_config,
            "memory_len": MEMORY_LEN,
            "total": result["total"],
            "cracked": result["cracked"],
            "crack_rate": result["crack_rate"],
            "elapsed_sec": result["elapsed_sec"],
            "analysis": result["analysis"],
            "beam_width": args.beam_width,
            "max_depth": args.max_depth,
            "max_pairs": args.max_pairs,
            "eval_batch_size": args.eval_batch_size,
            "details": result["details"],
        }
        output_dir = os.path.dirname(args.output)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(save_object, handle, ensure_ascii=False, indent=2)
        print(f"[结果已保存] -> {args.output}")


if __name__ == "__main__":
    main()
