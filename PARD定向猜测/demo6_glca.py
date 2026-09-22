"""Train the two-stage password model with a GLCA teacher encoder.

This module contains the vocabulary, data loader, source-only student, decoder,
losses, and two-stage trainer. Runtime dependencies are PyTorch,
torch-geometric, and tqdm. The teacher path is:

    (src, tgt) -> shared sequence encoding
               -> bidirectional gated latent cross-attention
               -> 128-D teacher latent

PyG ``HeteroData`` is retained only as a variable-length pair container.  The
containers created here have no ``edge_index`` fields, so no sequential,
Levenshtein-alignment, character-match, or density features are computed.
"""

import argparse
import json
import math
import os
import random
import time
from collections import Counter
from typing import Dict, List, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.optim.lr_scheduler import OneCycleLR
from torch.utils.data import Dataset, DataLoader, random_split
from torch_geometric.data import Batch, HeteroData

from tqdm import tqdm


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GLCA_ARCHITECTURE = "GatedLatentCrossAttention_Teacher_SourceStudent_TwoStage"
DEFAULT_CROSS_LATENT_DIM = 64

# Vocabulary, model defaults, and inference API shared with eval6_glca.py.
ASCII_START          = 32
ASCII_END            = 127
NUM_ASCII_CHARS      = ASCII_END - ASCII_START          # 95
PAD_IDX              = NUM_ASCII_CHARS + 1              # 96
CHAR_VOCAB_SIZE      = NUM_ASCII_CHARS + 2              # 97
MAX_LEN              = 20
EMBED_DIM            = 256
HGT_HIDDEN_DIM       = 256
LATENT_DIM           = 128
NUM_HGT_LAYERS       = 3
NUM_HEADS            = 4
FC_HIDDEN_DIM        = 512
DROPOUT              = 0.1
LEARNING_RATE        = 1e-3
WEIGHT_DECAY         = 1e-5
BATCH_SIZE           = 256
NUM_EPOCHS           = 7
GRAD_CLIP            = 1.0
VALID_RATIO          = 0.0
COSINE_SIM_THRESHOLD = 0.0
TGT_PAD         = 0
TGT_BOS         = 1
TGT_EOS         = 2
TGT_CHAR_OFFSET = 3
TGT_VOCAB_SIZE  = NUM_ASCII_CHARS + 3     # 98
MAX_TGT_LEN     = MAX_LEN + 2            # 22
NUM_DEC_LAYERS  = 3
MEMORY_LEN      = MAX_LEN                # 20（单通道，与推理一致）


def set_global_seed(seed: int) -> None:
    """Best-effort reproducibility for a single controlled ablation run."""
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    if hasattr(torch.backends, 'cudnn'):
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True


def char_to_idx(c: str) -> int:
    o = ord(c)
    if ASCII_START <= o < ASCII_END:
        return o - ASCII_START
    raise ValueError(f"字符 {repr(c)} (ord={o}) 超出可打印 ASCII 范围")


def idx_to_char(idx: int) -> str:
    if 0 <= idx < NUM_ASCII_CHARS:
        return chr(idx + ASCII_START)
    raise ValueError(f"字符索引 {idx} 越界 [0, {NUM_ASCII_CHARS-1}]")


def is_valid_password(pw: str) -> bool:
    return (1 <= len(pw) <= MAX_LEN
            and all(ASCII_START <= ord(c) < ASCII_END for c in pw))


def password_to_indices(pw: str) -> List[int]:
    return [char_to_idx(c) for c in pw]


def _bigrams(pw: str) -> Counter:
    s = '\x01' + pw + '\x02'
    return Counter(s[i:i+2] for i in range(len(s)-1))


def cosine_sim(pw1: str, pw2: str) -> float:
    c1, c2 = _bigrams(pw1), _bigrams(pw2)
    grams  = set(c1) | set(c2)
    dot    = sum(c1[g] * c2[g] for g in grams)
    n1     = math.sqrt(sum(v*v for v in c1.values()))
    n2     = math.sqrt(sum(v*v for v in c2.values()))
    return dot / (n1 * n2) if n1 and n2 else 0.0


def load_checkpoint(
    model,
    ckpt_path: str,
    device:    Optional[torch.device] = None,
):
    if device is None:
        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    ckpt = torch.load(ckpt_path, map_location=device)
    model.load_state_dict(ckpt['model_state'])
    epoch = ckpt.get('epoch', 'NA')
    valid_loss = ckpt.get('valid_loss', float('nan'))
    train_loss = ckpt.get('train_loss', float('nan'))
    print(f"[加载] epoch={epoch}  train_loss={train_loss:.4f}  valid_loss={valid_loss:.4f}")
    return model.to(device)


def encode_tgt(pw: str) -> Optional[torch.Tensor]:
    if not is_valid_password(pw):
        return None
    indices = [TGT_BOS]
    for c in pw:
        try:
            indices.append(char_to_idx(c) + TGT_CHAR_OFFSET)
        except ValueError:
            return None
    indices.append(TGT_EOS)
    if len(indices) > MAX_TGT_LEN:
        return None
    indices += [TGT_PAD] * (MAX_TGT_LEN - len(indices))
    return torch.tensor(indices, dtype=torch.long)


def decode_tgt(token_list: List[int]) -> str:
    chars = []
    for tok in token_list:
        if tok == TGT_EOS:
            break
        if TGT_CHAR_OFFSET <= tok < TGT_CHAR_OFFSET + NUM_ASCII_CHARS:
            chars.append(idx_to_char(tok - TGT_CHAR_OFFSET))
    return ''.join(chars)


def build_pair_container(
    src_indices: List[int], tgt_indices: List[int]
) -> HeteroData:
    """Store a password pair without constructing any hand-designed edges."""
    data = HeteroData()
    data["orig"].x = torch.tensor(src_indices, dtype=torch.long)
    data["curr"].x = torch.tensor(tgt_indices, dtype=torch.long)
    return data


def build_graph(
    orig_aligned: List[int],
    curr_aligned: Optional[List[int]] = None,
    **_ignored_edge_options,
) -> HeteroData:
    """Compatibility builder for source-only inference; it creates no edges."""
    curr = orig_aligned if curr_aligned is None else curr_aligned
    return build_pair_container(orig_aligned, curr)


class GatedLatentCrossAttentionBlock(nn.Module):
    """Shared self-encoding followed by bidirectional low-rank interaction.

    Queries, keys, and values are compressed from ``hidden_dim`` to
    ``cross_latent_dim``. The attended context is projected back to the hidden
    space, where a sigmoid gate controls how much cross-password information is
    injected into each token. The same parameters are used in both directions.
    """

    def __init__(
        self,
        hidden_dim: int,
        cross_latent_dim: int,
        num_heads: int,
        ff_dim: int,
        dropout: float,
    ):
        super().__init__()
        if hidden_dim <= 0 or cross_latent_dim <= 0 or ff_dim <= 0:
            raise ValueError("hidden_dim, cross_latent_dim, and ff_dim must be positive")
        if num_heads <= 0:
            raise ValueError("num_heads must be positive")
        if hidden_dim % num_heads != 0:
            raise ValueError(
                f"hidden_dim={hidden_dim} must be divisible by num_heads={num_heads}"
            )
        if cross_latent_dim % num_heads != 0:
            raise ValueError(
                "cross_latent_dim="
                f"{cross_latent_dim} must be divisible by num_heads={num_heads}"
            )

        self.hidden_dim = hidden_dim
        self.cross_latent_dim = cross_latent_dim
        self.num_heads = num_heads
        self.query_head_dim = cross_latent_dim // num_heads
        self.value_head_dim = self.query_head_dim

        # One shared sequence encoder E is applied independently to src/tgt.
        self.self_norm = nn.LayerNorm(hidden_dim)
        self.self_attn = nn.MultiheadAttention(
            hidden_dim,
            num_heads,
            dropout=dropout,
            batch_first=True,
        )
        self.self_ffn_norm = nn.LayerNorm(hidden_dim)
        self.self_ffn = nn.Sequential(
            nn.Linear(hidden_dim, ff_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(ff_dim, hidden_dim),
        )

        # K/V share the explicit compressed latent C = W_c H.
        self.cross_norm = nn.LayerNorm(hidden_dim)
        self.q_proj = nn.Linear(hidden_dim, cross_latent_dim, bias=False)
        self.kv_compress = nn.Linear(hidden_dim, cross_latent_dim, bias=False)
        self.v_proj = nn.Linear(cross_latent_dim, cross_latent_dim, bias=False)
        self.out_proj = nn.Linear(cross_latent_dim, hidden_dim, bias=False)
        self.gate_proj = nn.Linear(2 * hidden_dim, hidden_dim)
        self.cross_out_norm = nn.LayerNorm(hidden_dim)

        self.attn_dropout = nn.Dropout(dropout)
        self.resid_dropout = nn.Dropout(dropout)
        self.collect_gate_stats = False
        self.last_gate_stats: Dict[str, torch.Tensor] = {}

    @staticmethod
    def _zero_padding(x: torch.Tensor, pad_mask: torch.Tensor) -> torch.Tensor:
        return x.masked_fill(pad_mask.unsqueeze(-1), 0.0)

    def _self_encode(
        self, x: torch.Tensor, pad_mask: torch.Tensor
    ) -> torch.Tensor:
        normed = self.self_norm(x)
        attended, _ = self.self_attn(
            normed,
            normed,
            normed,
            key_padding_mask=pad_mask,
            need_weights=False,
        )
        x = x + self.resid_dropout(attended)
        x = x + self.resid_dropout(self.self_ffn(self.self_ffn_norm(x)))
        return self._zero_padding(x, pad_mask)

    def _latent_cross_attention(
        self,
        query_tokens: torch.Tensor,
        context_tokens: torch.Tensor,
        query_pad: torch.Tensor,
        context_pad: torch.Tensor,
    ) -> torch.Tensor:
        batch_size, query_len, _ = query_tokens.shape
        context_len = context_tokens.size(1)

        query_norm = self.cross_norm(query_tokens)
        context_norm = self.cross_norm(context_tokens)

        q = self.q_proj(query_norm).view(
            batch_size, query_len, self.num_heads, self.query_head_dim
        ).transpose(1, 2)
        compressed_context = self.kv_compress(context_norm)
        k = compressed_context.view(
            batch_size, context_len, self.num_heads, self.query_head_dim
        ).transpose(1, 2)
        v = self.v_proj(compressed_context).view(
            batch_size, context_len, self.num_heads, self.value_head_dim
        ).transpose(1, 2)

        # Do the dot product itself in FP32: casting after a half-precision
        # matmul would not recover values that had already overflowed.
        scores_fp32 = torch.matmul(q.float(), k.float().transpose(-2, -1))
        scores_fp32 = scores_fp32 / math.sqrt(self.query_head_dim)
        key_valid = (~context_pad)[:, None, None, :]

        # Keep masking and normalization in FP32 for stable AMP behavior.
        scores_fp32 = scores_fp32.masked_fill(~key_valid, -1.0e9)
        weights = torch.softmax(scores_fp32, dim=-1)
        weights = weights * key_valid.to(weights.dtype)
        weights = weights / weights.sum(dim=-1, keepdim=True).clamp_min(1.0e-9)
        weights = self.attn_dropout(weights.to(v.dtype))

        context = torch.matmul(weights, v)
        context = context.transpose(1, 2).contiguous().view(
            batch_size, query_len, self.cross_latent_dim
        )
        context = self.out_proj(context)
        return self._zero_padding(context, query_pad)

    @staticmethod
    def _gate_mean(
        gate: torch.Tensor, pad_mask: torch.Tensor
    ) -> torch.Tensor:
        valid = (~pad_mask).unsqueeze(-1).expand_as(gate).float()
        numerator = (gate.detach().float() * valid).sum()
        denominator = valid.sum().clamp_min(1.0)
        return numerator / denominator

    def _inject(
        self,
        tokens: torch.Tensor,
        cross_context: torch.Tensor,
        pad_mask: torch.Tensor,
    ) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        gate = torch.sigmoid(self.gate_proj(torch.cat([tokens, cross_context], dim=-1)))
        updated = self.cross_out_norm(
            tokens + self.resid_dropout(gate * cross_context)
        )
        updated = self._zero_padding(updated, pad_mask)
        gate_mean = self._gate_mean(gate, pad_mask) if self.collect_gate_stats else None
        return updated, gate_mean

    def forward(
        self,
        src: torch.Tensor,
        tgt: torch.Tensor,
        src_pad: torch.Tensor,
        tgt_pad: torch.Tensor,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        src_encoded = self._self_encode(src, src_pad)
        tgt_encoded = self._self_encode(tgt, tgt_pad)

        # Both directions are computed from the same pre-injection states.
        src_from_tgt = self._latent_cross_attention(
            src_encoded, tgt_encoded, src_pad, tgt_pad
        )
        tgt_from_src = self._latent_cross_attention(
            tgt_encoded, src_encoded, tgt_pad, src_pad
        )

        src_out, src_gate = self._inject(src_encoded, src_from_tgt, src_pad)
        tgt_out, tgt_gate = self._inject(tgt_encoded, tgt_from_src, tgt_pad)
        self.last_gate_stats = (
            {
                "src_from_tgt": src_gate,
                "tgt_from_src": tgt_gate,
            }
            if self.collect_gate_stats
            else {}
        )
        return src_out, tgt_out


class Pass2EditGLCA(nn.Module):
    """Pair-aware GLCA teacher with the original source student and decoder."""

    def __init__(
        self,
        char_vocab_size: int = CHAR_VOCAB_SIZE,
        embed_dim: int = EMBED_DIM,
        hgt_hidden: int = HGT_HIDDEN_DIM,
        num_enc_layers: int = NUM_HGT_LAYERS,
        num_dec_layers: int = NUM_DEC_LAYERS,
        num_heads: int = NUM_HEADS,
        fc_dim: int = FC_HIDDEN_DIM,
        tgt_vocab_size: int = TGT_VOCAB_SIZE,
        max_tgt_len: int = MAX_TGT_LEN,
        dropout: float = DROPOUT,
        pad_idx: int = PAD_IDX,
        latent_dim: int = LATENT_DIM,
        cross_latent_dim: int = DEFAULT_CROSS_LATENT_DIM,
        use_view_embed: bool = True,
    ):
        super().__init__()
        if num_enc_layers < 1:
            raise ValueError("num_enc_layers must be at least 1")
        self.dropout_p = dropout
        self.hgt_hidden = hgt_hidden
        self.latent_dim = latent_dim
        self.cross_latent_dim = cross_latent_dim
        self.teacher_num_layers = num_enc_layers
        self.num_heads = num_heads
        self.use_view_embed = use_view_embed
        self.use_density_gate = False
        dec_dim = hgt_hidden

        # The shared input embedding and the complete deployable path retain
        # their original names/shapes for a controlled teacher-only change.
        self.char_embed = nn.Embedding(
            char_vocab_size, embed_dim, padding_idx=pad_idx
        )
        self.char_pos_embed = nn.Embedding(MAX_LEN, embed_dim)
        self.view_embed = nn.Embedding(2, embed_dim) if use_view_embed else None

        self.teacher_input_proj = (
            nn.Identity()
            if embed_dim == hgt_hidden
            else nn.Linear(embed_dim, hgt_hidden)
        )
        self.teacher_blocks = nn.ModuleList(
            [
                GatedLatentCrossAttentionBlock(
                    hidden_dim=hgt_hidden,
                    cross_latent_dim=cross_latent_dim,
                    num_heads=num_heads,
                    ff_dim=fc_dim,
                    dropout=dropout,
                )
                for _ in range(num_enc_layers)
            ]
        )
        self.teacher_head = nn.Sequential(
            nn.Linear(2 * hgt_hidden, fc_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(fc_dim, latent_dim),
            nn.LayerNorm(latent_dim),
        )

        student_layer = nn.TransformerEncoderLayer(
            d_model=embed_dim,
            nhead=num_heads,
            dim_feedforward=fc_dim,
            dropout=dropout,
            batch_first=True,
            norm_first=True,
        )
        self.student_encoder = nn.TransformerEncoder(student_layer, num_layers=2)
        self.student_head = nn.Sequential(
            nn.Linear(embed_dim, fc_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(fc_dim, latent_dim),
            nn.LayerNorm(latent_dim),
        )

        self.memory_proj = nn.Linear(embed_dim + latent_dim, dec_dim)
        self.memory_norm = nn.LayerNorm(dec_dim)
        self.tgt_embed = nn.Embedding(
            tgt_vocab_size, dec_dim, padding_idx=TGT_PAD
        )
        self.pos_embed = nn.Embedding(max_tgt_len + 1, dec_dim)
        decoder_layer = nn.TransformerDecoderLayer(
            d_model=dec_dim,
            nhead=num_heads,
            dim_feedforward=fc_dim,
            dropout=dropout,
            batch_first=True,
            norm_first=True,
        )
        self.seq_decoder = nn.TransformerDecoder(
            decoder_layer, num_layers=num_dec_layers
        )
        self.fc_out = nn.Linear(dec_dim, tgt_vocab_size)
        self._init_weights()

    def _init_weights(self):
        nn.init.xavier_uniform_(self.fc_out.weight)
        nn.init.zeros_(self.fc_out.bias)
        if self.view_embed is not None:
            nn.init.normal_(self.view_embed.weight, std=0.02)

    @staticmethod
    def _node_position_ids(batch_index: torch.Tensor) -> torch.Tensor:
        """Create zero-based position ids for every graph in a flat batch."""
        pos = torch.zeros_like(batch_index)
        if batch_index.numel() == 0:
            return pos
        for graph_id in range(int(batch_index.max().item()) + 1):
            mask = batch_index == graph_id
            pos[mask] = torch.arange(mask.sum(), device=batch_index.device)
        return pos.clamp(max=MAX_LEN - 1)

    def _embed_nodes(
        self, indices: torch.Tensor, batch_index: torch.Tensor, view_id: Optional[int] = None
    ) -> torch.Tensor:
        pos = self._node_position_ids(batch_index)
        x = self.char_embed(indices) + self.char_pos_embed(pos)
        if view_id is not None and self.view_embed is not None:
            x = x + self.view_embed(torch.full_like(indices, view_id))
        return x

    def _pack_flat(
        self,
        feats:       torch.Tensor,
        batch_index: torch.Tensor,
        num_graphs: int,
        device:     torch.device,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """Pack flat per-node features as ``(B, MAX_LEN, H)``."""
        B, H = num_graphs, feats.size(-1)
        memory = torch.zeros(B, MAX_LEN, H, device=device)
        mask   = torch.ones(B, MAX_LEN, dtype=torch.bool, device=device)

        for i in range(B):
            node_feats = feats[batch_index == i]
            L = min(node_feats.size(0), MAX_LEN)
            memory[i, :L] = node_feats[:L]
            mask[i, :L]   = False

        return memory, mask

    def _source_tokens(self, batch) -> Tuple[torch.Tensor, torch.Tensor]:
        batch_index = batch['orig'].batch
        tokens = self._embed_nodes(batch['orig'].x, batch_index)
        return self._pack_flat(
            tokens, batch_index, batch.num_graphs, batch['orig'].x.device
        )

    def encode_student(self, batch) -> torch.Tensor:
        src_tokens, src_pad = self._source_tokens(batch)
        encoded = self.student_encoder(src_tokens, src_key_padding_mask=src_pad)
        valid = (~src_pad).unsqueeze(-1).to(encoded.dtype)
        pooled = (encoded * valid).sum(dim=1) / valid.sum(dim=1).clamp(min=1.0)
        return self.student_head(pooled)

    def memory_from_latent(
        self, batch, latent: torch.Tensor
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        src_tokens, src_pad = self._source_tokens(batch)
        latent_tokens = latent.unsqueeze(1).expand(-1, src_tokens.size(1), -1)
        memory = self.memory_norm(self.memory_proj(torch.cat([src_tokens, latent_tokens], dim=-1)))
        return memory, src_pad

    def encode(self, batch) -> Tuple[torch.Tensor, torch.Tensor]:
        """Student-only encoder used by evaluation and deployment."""
        return self.memory_from_latent(batch, self.encode_student(batch))

    def decode(
        self,
        tgt_seq:              torch.Tensor,
        memory:               torch.Tensor,
        mem_key_padding_mask: torch.Tensor,
    ) -> torch.Tensor:
        B, T = tgt_seq.shape
        device = tgt_seq.device

        pos = torch.arange(T, device=device).unsqueeze(0)
        emb = self.tgt_embed(tgt_seq) + self.pos_embed(pos)

        causal_mask = nn.Transformer.generate_square_subsequent_mask(T, device=device)

        out = self.seq_decoder(
            emb, memory,
            tgt_mask=causal_mask,
            memory_key_padding_mask=mem_key_padding_mask,
        )

        return self.fc_out(out)  # (B, T, 98)

    def decode_with_latent(
        self, batch, tgt_seq: torch.Tensor, latent: torch.Tensor
    ) -> torch.Tensor:
        memory, mem_pad = self.memory_from_latent(batch, latent)
        return self.decode(tgt_seq[:, :-1], memory, mem_pad)

    def forward_teacher(self, pair_batch, tgt_seq: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        h_t = self.encode_teacher(pair_batch)
        return self.decode_with_latent(pair_batch, tgt_seq, h_t), h_t

    def forward_student(self, batch, tgt_seq: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        h_s = self.encode_student(batch)
        return self.decode_with_latent(batch, tgt_seq, h_s), h_s

    def forward(self, batch, tgt_seq: torch.Tensor) -> torch.Tensor:
        """Backward-compatible forward defaults to the deployable student."""
        memory, mem_pad = self.encode(batch)
        return self.decode(tgt_seq[:, :-1], memory, mem_pad)

    def student_stage_modules(self) -> tuple:
        """Modules optimized by the weighted Stage-2 objective."""
        return (
            self.student_encoder,
            self.student_head,
            self.memory_proj,
            self.memory_norm,
            self.tgt_embed,
            self.pos_embed,
            self.seq_decoder,
            self.fc_out,
        )

    def student_only_stage_modules(self) -> tuple:
        """Complete deployable path trained from scratch by ``L_s`` only."""
        return (
            self.char_embed,
            self.char_pos_embed,
            *self.student_stage_modules(),
        )

    def set_trainable_stage(self, stage: str):
        """Set the exact parameter scope for Teacher, Student, or scratch."""
        if stage not in {'teacher', 'student', 'student_only'}:
            raise ValueError(f"unknown training stage: {stage}")
        if stage == 'teacher':
            for p in self.parameters():
                p.requires_grad = True
            for module in (self.student_encoder, self.student_head):
                for p in module.parameters():
                    p.requires_grad = False
        else:
            for p in self.parameters():
                p.requires_grad = False
            # Stage 2 keeps the target-aware teacher and all shared input
            # embeddings fixed, while letting the deployable student path and
            # decoder adapt to h_s. L_distill still has no decoder dependency,
            # so only L_s updates the decoder.
            modules = (self.student_only_stage_modules()
                       if stage == 'student_only'
                       else self.student_stage_modules())
            for module in modules:
                for p in module.parameters():
                    p.requires_grad = True

    def num_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)

    def _encode_pair_tokens(
        self, pair_batch
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        src_batch = pair_batch["orig"].batch
        tgt_batch = pair_batch["curr"].batch
        num_graphs = pair_batch.num_graphs
        device = pair_batch["orig"].x.device

        src_flat = self._embed_nodes(pair_batch["orig"].x, src_batch, 0)
        tgt_flat = self._embed_nodes(pair_batch["curr"].x, tgt_batch, 1)
        src, src_pad = self._pack_flat(
            src_flat, src_batch, num_graphs, device
        )
        tgt, tgt_pad = self._pack_flat(
            tgt_flat, tgt_batch, num_graphs, device
        )
        src = self.teacher_input_proj(src)
        tgt = self.teacher_input_proj(tgt)

        for block in self.teacher_blocks:
            src, tgt = block(src, tgt, src_pad, tgt_pad)
        return src, tgt, src_pad, tgt_pad

    @staticmethod
    def _masked_mean(tokens: torch.Tensor, pad_mask: torch.Tensor) -> torch.Tensor:
        valid = (~pad_mask).unsqueeze(-1).to(tokens.dtype)
        return (tokens * valid).sum(dim=1) / valid.sum(dim=1).clamp_min(1.0)

    def encode_teacher(self, pair_batch) -> torch.Tensor:
        src, tgt, src_pad, tgt_pad = self._encode_pair_tokens(pair_batch)
        src_pool = self._masked_mean(src, src_pad)
        tgt_pool = self._masked_mean(tgt, tgt_pad)
        pair_repr = torch.cat(
            [tgt_pool - src_pool, src_pool * tgt_pool], dim=-1
        )
        return self.teacher_head(pair_repr)

    def get_gate_statistics(self) -> List[Dict[str, object]]:
        return [
            {
                "layer": index + 1,
                "gate": {
                    name: value.float().cpu().item()
                    for name, value in block.last_gate_stats.items()
                },
            }
            for index, block in enumerate(self.teacher_blocks)
        ]

    def set_gate_statistics_enabled(self, enabled: bool) -> None:
        for block in self.teacher_blocks:
            block.collect_gate_stats = enabled
            if not enabled:
                block.last_gate_stats = {}

    def print_gate_statistics(self) -> None:
        stats = self.get_gate_statistics()
        if not stats or not any(item["gate"] for item in stats):
            print("[GLCA Gate] 暂无统计；需先完成一次 teacher forward。")
            return
        for item in stats:
            text = "  ".join(
                f"{name}={value:.3f}"
                for name, value in item["gate"].items()
            )
            print(f"[GLCA Gate] Layer {item['layer']}: {text}")


# Compatibility names used by the training/evaluation code.
Pass2EditSeq2Seq = Pass2EditGLCA
Pass2EditHGT = Pass2EditGLCA


class PasswordPairDataset(Dataset):
    """Parse JSONL pairs and construct edge-free teacher containers."""

    def __init__(
        self,
        jsonl_path:    str,
        sim_threshold: float = COSINE_SIM_THRESHOLD,
        max_pairs:     Optional[int] = None,
        verbose:       bool = True,
    ):
        self.samples: List[Tuple[List[int], List[int], torch.Tensor]] = []
        n_loaded = n_valid = n_ok = 0

        with open(jsonl_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                src, tgt = obj.get('src', ''), obj.get('tgt', '')
                n_loaded += 1

                if not is_valid_password(src) or not is_valid_password(tgt):
                    continue
                n_valid += 1

                if sim_threshold > 0.0 and cosine_sim(src, tgt) <= sim_threshold:
                    continue

                tgt_tensor = encode_tgt(tgt)
                if tgt_tensor is None:
                    continue

                self.samples.append((
                    password_to_indices(src), password_to_indices(tgt), tgt_tensor
                ))
                n_ok += 1

                if max_pairs and n_ok >= max_pairs:
                    break

        if verbose:
            print(f"[Seq2SeqDataset] {jsonl_path}")
            print(f"  读取: {n_loaded:,}  字符合法: {n_valid:,}  训练样本: {n_ok:,}")

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, index):
        src_indices, tgt_indices, tgt_tensor = self.samples[index]
        return build_pair_container(src_indices, tgt_indices), tgt_tensor


def _collate_pairs(batch):
    pairs, targets = zip(*batch)
    return Batch.from_data_list(list(pairs)), torch.stack(list(targets), dim=0)


def create_seq2seq_dataloaders(
    jsonl_path: str,
    batch_size: int = BATCH_SIZE,
    valid_ratio: float = VALID_RATIO,
    sim_threshold: float = COSINE_SIM_THRESHOLD,
    max_pairs: Optional[int] = None,
    num_workers: int = 0,
    seed: int = 42,
) -> Tuple[DataLoader, DataLoader]:
    dataset = PasswordPairDataset(
        jsonl_path,
        sim_threshold=sim_threshold,
        max_pairs=max_pairs,
    )
    total = len(dataset)
    valid_count = int(round(total * valid_ratio)) if valid_ratio > 0 else 0
    if valid_count >= total and total > 0:
        valid_count = total - 1
    train_count = total - valid_count
    generator = torch.Generator().manual_seed(seed)
    train_set, valid_set = random_split(
        dataset, [train_count, valid_count], generator=generator
    )
    loader_options = {
        "batch_size": batch_size,
        "num_workers": num_workers,
        "collate_fn": _collate_pairs,
    }
    print(f"[DataLoader/edge-free] 训练: {train_count:,}  验证: {valid_count:,}")
    return (
        DataLoader(train_set, shuffle=True, **loader_options),
        DataLoader(valid_set, shuffle=False, **loader_options),
    )


def _check_stage1_checkpoint(path: str, expected_config: dict) -> None:
    checkpoint = torch.load(path, map_location="cpu")
    if checkpoint.get("stage") != "teacher":
        raise ValueError(
            "--stage1-checkpoint must be a Stage-1 Teacher checkpoint; "
            f"found stage={checkpoint.get('stage')!r}"
        )
    config = checkpoint.get("model_config", {}) or {}
    architecture = config.get("architecture")
    if architecture != GLCA_ARCHITECTURE:
        raise ValueError(
            "--stage1-checkpoint must be a GLCA checkpoint; "
            f"found architecture={architecture!r}"
        )
    shape_keys = (
        "cross_latent_dim",
        "teacher_num_layers",
        "num_heads",
        "embed_dim",
        "hidden_dim",
        "fc_dim",
        "num_dec_layers",
        "latent_dim",
        "use_view_embed",
    )
    mismatches = [
        f"{key}: checkpoint={config.get(key)!r}, requested={expected_config.get(key)!r}"
        for key in shape_keys
        if config.get(key) != expected_config.get(key)
    ]
    if mismatches:
        raise ValueError(
            "Stage-1 GLCA configuration mismatch:\n  " + "\n  ".join(mismatches)
        )


def _run_epoch_two_stage(
    model:     Pass2EditGLCA,
    loader:    DataLoader,
    optimizer,
    criterion: nn.CrossEntropyLoss,
    scheduler,
    device:    torch.device,
    train:     bool,
    stage:     str,
    distill_weight: float = 1.0,
) -> dict:
    if stage == 'teacher':
        model.train(train)
    else:
        # Keep the teacher path deterministic; enable train mode only for the
        # student and decoder modules optimized in stage 2.
        model.eval()
        if train:
            modules = (model.student_only_stage_modules()
                       if stage == 'student_only'
                       else model.student_stage_modules())
            for module in modules:
                module.train()

    total_loss = total_gen = total_distill = 0.0
    total_correct = total_n = 0

    stage_name = ("Teacher" if stage == 'teacher'
                  else "Student-only" if stage == 'student_only'
                  else "Student")
    pbar = tqdm(loader, desc=f"{stage_name}-{'训练' if train else '验证'}",
                leave=False, dynamic_ncols=True)

    ctx = torch.enable_grad() if train else torch.no_grad()
    with ctx:
        for batch_graph, tgt_batch in pbar:
            batch_graph = batch_graph.to(device)
            tgt_batch   = tgt_batch.to(device)

            if stage == 'teacher':
                logits, _ = model.forward_teacher(batch_graph, tgt_batch)
                distill_loss = logits.new_zeros(())
            else:
                logits, h_s = model.forward_student(batch_graph, tgt_batch)
                if stage == 'student' and distill_weight > 0.0:
                    with torch.no_grad():
                        h_t = model.encode_teacher(batch_graph)
                    # Both heads end in LayerNorm, so mean MSE is naturally scaled.
                    distill_loss = F.mse_loss(h_s, h_t.detach())
                else:
                    # lambda=0 and Student-only avoid an unnecessary teacher pass.
                    distill_loss = logits.new_zeros(())

            labels = tgt_batch[:, 1:]

            B, T1, V = logits.shape
            generation_loss = criterion(
                logits.contiguous().view(B * T1, V),
                labels.contiguous().view(-1),
            )
            loss = (generation_loss if stage == 'teacher'
                    else generation_loss + distill_weight * distill_loss)

            if train:
                optimizer.zero_grad()
                loss.backward()
                trainable = [p for p in model.parameters() if p.requires_grad]
                nn.utils.clip_grad_norm_(trainable, GRAD_CLIP)
                optimizer.step()
                if scheduler is not None:
                    scheduler.step()

            pad_mask = (labels != TGT_PAD)
            correct  = ((logits.argmax(-1) == labels) & pad_mask).sum().item()
            n_tok    = pad_mask.sum().item()

            total_loss    += loss.item() * B
            total_gen     += generation_loss.item() * B
            total_distill += distill_loss.item() * B
            total_correct += correct
            total_n       += n_tok

            postfix = {'loss': f"{loss.item():.4f}",
                       'acc': f"{correct / max(n_tok, 1):.2%}"}
            if stage == 'student':
                postfix.update({'Ls': f"{generation_loss.item():.4f}",
                                'Ld': f"{distill_loss.item():.4f}",
                                'lambda': f"{distill_weight:g}"})
            pbar.set_postfix(postfix)

    denom = max(len(loader.dataset), 1)
    return {
        'loss': total_loss / denom,
        'generation_loss': total_gen / denom,
        'distill_loss': total_distill / denom,
        'accuracy': total_correct / max(total_n, 1),
    }


def train_seq2seq(
    model: Pass2EditGLCA,
    train_loader: DataLoader,
    valid_loader: DataLoader,
    save_path: str = "best_model_glca.pt",
    teacher_epochs: int = NUM_EPOCHS,
    student_epochs: int = NUM_EPOCHS,
    device: Optional[torch.device] = None,
    save_each_epoch: bool = False,
    log_gate_stats: bool = False,
    model_config: Optional[dict] = None,
    training_mode: str = "full",
    distill_weight: float = 1.0,
    stage1_checkpoint: Optional[str] = None,
    seed: int = 42,
) -> dict:
    """Original two-stage optimization with GLCA-specific checkpoint metadata."""
    if training_mode not in {"full", "warm_start_lambda0", "student_only"}:
        raise ValueError(f"unknown training mode: {training_mode}")
    if distill_weight < 0:
        raise ValueError("distill_weight must be non-negative")
    if training_mode == "warm_start_lambda0":
        if distill_weight != 0.0:
            raise ValueError("warm_start_lambda0 requires distill_weight=0")
        if not stage1_checkpoint:
            raise ValueError("warm_start_lambda0 requires --stage1-checkpoint")
    if training_mode == "student_only" and distill_weight != 0.0:
        raise ValueError("student_only requires distill_weight=0")

    if device is None:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    model = model.to(device)
    model.set_gate_statistics_enabled(log_gate_stats)
    criterion = nn.CrossEntropyLoss(
        ignore_index=TGT_PAD,
        label_smoothing=0.1,
    )
    has_valid = len(valid_loader.dataset) > 0
    save_dir = os.path.dirname(save_path) or "."
    os.makedirs(save_dir, exist_ok=True)
    stem, extension = os.path.splitext(save_path)
    extension = extension or ".pt"
    teacher_path = f"{stem}_teacher{extension}"
    last_path = f"{stem}_last{extension}"
    history = {
        "teacher": {},
        "student": {},
        "training_mode": training_mode,
        "distill_weight": distill_weight,
        "seed": seed,
    }

    def make_optimizer(stage_epochs: int):
        parameters = [p for p in model.parameters() if p.requires_grad]
        optimizer = optim.Adam(
            parameters,
            lr=LEARNING_RATE,
            weight_decay=WEIGHT_DECAY,
        )
        scheduler = OneCycleLR(
            optimizer,
            max_lr=LEARNING_RATE,
            steps_per_epoch=max(len(train_loader), 1),
            epochs=stage_epochs,
            pct_start=0.1,
            anneal_strategy="cos",
            div_factor=25.0,
            final_div_factor=1000.0,
        )
        return optimizer, scheduler

    def save_checkpoint(
        path: str,
        stage: str,
        epoch: int,
        train_stats: dict,
        valid_stats: dict,
        optimizer,
        tag: str,
    ) -> None:
        torch.save(
            {
                "epoch": epoch,
                "stage": stage,
                "model_state": model.state_dict(),
                "optim_state": optimizer.state_dict(),
                "train_loss": train_stats["loss"],
                "train_acc": train_stats["accuracy"],
                "valid_loss": valid_stats["loss"],
                "valid_acc": valid_stats["accuracy"],
                "train_stats": train_stats,
                "valid_stats": valid_stats,
                "model_class": "Pass2Edit_GLCA_TwoStage",
                "architecture": GLCA_ARCHITECTURE,
                "model_config": model_config or {},
                "selection_tag": tag,
            },
            path,
        )
        print(f"  [saved] 模型已保存 [{tag}] -> {path}")

    def empty_valid() -> dict:
        return {
            "loss": float("nan"),
            "generation_loss": float("nan"),
            "distill_loss": float("nan"),
            "accuracy": float("nan"),
        }

    def run_stage(stage: str, epochs: int, best_path: str) -> dict:
        # Keep the controlled ablation RNG schedule stable across stages.
        set_global_seed(seed if stage == "teacher" else seed + 1)
        model.set_trainable_stage(stage)
        optimizer, scheduler = make_optimizer(epochs)
        stage_history = {
            "train_loss": [],
            "train_generation_loss": [],
            "train_distill_loss": [],
            "train_acc": [],
            "valid_loss": [],
            "valid_generation_loss": [],
            "valid_distill_loss": [],
            "valid_acc": [],
        }
        best_metric = float("inf")
        stage_label = (
            "1/2"
            if stage == "teacher"
            else "scratch"
            if stage == "student_only"
            else "2/2"
        )
        print(
            f"\n[Stage {stage_label}] {stage.upper()}  epochs={epochs}  "
            f"trainable_params={model.num_parameters():,}"
        )
        if stage == "student":
            print(f"  objective: Lgen + {distill_weight:g} * Ldistill")
            print("  frozen: GLCA teacher and shared source embeddings")
        elif stage == "student_only":
            print("  objective: Lgen (deployable source-only path from scratch)")

        for epoch in range(1, epochs + 1):
            start = time.time()
            train_stats = _run_epoch_two_stage(
                model,
                train_loader,
                optimizer,
                criterion,
                scheduler,
                device,
                True,
                stage,
                distill_weight,
            )
            valid_stats = (
                _run_epoch_two_stage(
                    model,
                    valid_loader,
                    None,
                    criterion,
                    None,
                    device,
                    False,
                    stage,
                    distill_weight,
                )
                if has_valid
                else empty_valid()
            )

            for prefix, stats in (
                ("train", train_stats),
                ("valid", valid_stats),
            ):
                stage_history[f"{prefix}_loss"].append(stats["loss"])
                stage_history[f"{prefix}_generation_loss"].append(
                    stats["generation_loss"]
                )
                stage_history[f"{prefix}_distill_loss"].append(
                    stats["distill_loss"]
                )
                stage_history[f"{prefix}_acc"].append(stats["accuracy"])

            valid_text = (
                f"V-Loss {valid_stats['loss']:.4f}  "
                f"V-Acc {valid_stats['accuracy']:.4f}"
                if has_valid
                else "V-Loss N/A  V-Acc N/A"
            )
            extra = (
                f"  Ls {train_stats['generation_loss']:.4f}  "
                f"Ld {train_stats['distill_loss']:.4f}  "
                f"lambda {distill_weight:g}"
                if stage == "student"
                else ""
            )
            print(
                f"{stage.capitalize()} Epoch {epoch:3d}/{epochs}  "
                f"Loss {train_stats['loss']:.4f}  "
                f"Acc {train_stats['accuracy']:.4f}{extra}  "
                f"{valid_text}  ({time.time() - start:.1f}s)"
            )

            if stage == "teacher" and log_gate_stats:
                model.print_gate_statistics()

            metric_source = valid_stats if has_valid else train_stats
            metric = metric_source["generation_loss"]
            if metric < best_metric:
                best_metric = metric
                save_checkpoint(
                    best_path,
                    stage,
                    epoch,
                    train_stats,
                    valid_stats,
                    optimizer,
                    (
                        "best_valid_generation_loss"
                        if has_valid
                        else "best_train_generation_loss"
                    ),
                )

            stage_last = (
                f"{stem}_teacher_last{extension}"
                if stage == "teacher"
                else last_path
            )
            save_checkpoint(
                stage_last,
                stage,
                epoch,
                train_stats,
                valid_stats,
                optimizer,
                "last",
            )
            if save_each_epoch:
                epoch_path = f"{stem}_{stage}_epoch{epoch}{extension}"
                save_checkpoint(
                    epoch_path,
                    stage,
                    epoch,
                    train_stats,
                    valid_stats,
                    optimizer,
                    f"{stage}_epoch{epoch}",
                )

        print(
            f"[{stage.upper()} complete] best "
            f"{'valid' if has_valid else 'train'} generation loss: "
            f"{best_metric:.4f}"
        )
        return stage_history

    print(
        f"[Training] mode={training_mode}  device={device}  total_params="
        f"{sum(p.numel() for p in model.parameters()):,}"
    )
    if training_mode == "student_only":
        history["teacher"] = {"skipped": True, "reason": "student_only"}
        history["student"] = run_stage(
            "student_only", student_epochs, save_path
        )
    else:
        if training_mode == "full":
            history["teacher"] = run_stage(
                "teacher", teacher_epochs, teacher_path
            )
            selected_stage1 = teacher_path
        else:
            selected_stage1 = os.path.abspath(stage1_checkpoint)
            if not os.path.isfile(selected_stage1):
                raise FileNotFoundError(
                    f"Stage-1 checkpoint does not exist: {selected_stage1}"
                )
            history["teacher"] = {
                "skipped": True,
                "reason": "reused_full_stage1",
                "checkpoint": selected_stage1,
            }

        teacher_checkpoint = torch.load(selected_stage1, map_location=device)
        if "model_state" not in teacher_checkpoint:
            raise ValueError(f"Invalid Stage-1 checkpoint: {selected_stage1}")
        model.load_state_dict(teacher_checkpoint["model_state"], strict=True)
        history["stage1_checkpoint"] = selected_stage1
        print(f"[Stage-1 loaded] {selected_stage1}")
        history["student"] = run_stage("student", student_epochs, save_path)

    history["train_loss"] = history["student"]["train_loss"]
    history["train_acc"] = history["student"]["train_acc"]
    history["valid_loss"] = history["student"]["valid_loss"]
    history["valid_acc"] = history["student"]["valid_acc"]
    return history


def cmd_train(args) -> None:
    if args.distill_weight is None:
        args.distill_weight = 1.0 if args.ablation == "full" else 0.0
    if args.ablation == "warm_start_lambda0" and not args.stage1_checkpoint:
        raise ValueError("warm_start_lambda0 requires --stage1-checkpoint")
    if args.ablation != "warm_start_lambda0" and args.stage1_checkpoint:
        raise ValueError(
            "--stage1-checkpoint is only valid for warm_start_lambda0"
        )
    if (
        args.ablation in {"warm_start_lambda0", "student_only"}
        and args.distill_weight != 0.0
    ):
        raise ValueError(f"{args.ablation} requires --distill-weight 0")

    set_global_seed(args.seed)
    train_loader, valid_loader = create_seq2seq_dataloaders(
        args.data,
        batch_size=args.batch_size,
        sim_threshold=args.sim_threshold,
        max_pairs=args.max_pairs,
        num_workers=args.workers,
        valid_ratio=args.valid_ratio,
        seed=args.seed,
    )

    model_config = {
        "architecture": GLCA_ARCHITECTURE,
        "teacher_encoder": "shared_self_attention_plus_bidirectional_glca",
        "teacher_pair_repr": "difference_product_no_direct_pools",
        "handcrafted_edges": False,
        "cross_latent_dim": args.cross_latent_dim,
        "teacher_num_layers": args.teacher_layers,
        "num_heads": NUM_HEADS,
        "embed_dim": EMBED_DIM,
        "hidden_dim": HGT_HIDDEN_DIM,
        "fc_dim": FC_HIDDEN_DIM,
        "num_dec_layers": NUM_DEC_LAYERS,
        "dropout": DROPOUT,
        "latent_dim": LATENT_DIM,
        "use_view_embed": not args.no_view_embed,
        "training_scheme": (
            "source_student_from_scratch"
            if args.ablation == "student_only"
            else "reuse_stage1_then_student"
            if args.ablation == "warm_start_lambda0"
            else "teacher_then_student"
        ),
        "ablation": args.ablation,
        "distill_weight": args.distill_weight,
        "student_loss": (
            f"Lgen_plus_{args.distill_weight:g}_Ldistill"
            if args.distill_weight > 0
            else "Lgen_only"
        ),
        "stage1_enabled": args.ablation != "student_only",
        "stage1_trained_this_run": args.ablation == "full",
        "stage1_reused": args.ablation == "warm_start_lambda0",
        "stage1_checkpoint": (
            os.path.abspath(args.stage1_checkpoint)
            if args.stage1_checkpoint
            else None
        ),
        "student_train_shared_embeddings": args.ablation == "student_only",
        "selection_metric": (
            "valid_generation_loss"
            if args.valid_ratio > 0
            else "train_generation_loss"
        ),
        "seed": args.seed,
    }

    if args.stage1_checkpoint:
        _check_stage1_checkpoint(args.stage1_checkpoint, model_config)

    model = Pass2EditGLCA(
        num_enc_layers=args.teacher_layers,
        cross_latent_dim=args.cross_latent_dim,
        use_view_embed=model_config["use_view_embed"],
    )
    device = torch.device(
        args.device
        if args.device
        else ("cuda" if torch.cuda.is_available() else "cpu")
    )
    print(f"模型参数量: {model.num_parameters():,}")
    print(
        "Teacher: edge-free bidirectional GLCA "
        f"(hidden={HGT_HIDDEN_DIM}, compressed={args.cross_latent_dim}, "
        f"layers={args.teacher_layers}, heads={NUM_HEADS})"
    )

    history = train_seq2seq(
        model,
        train_loader,
        valid_loader,
        save_path=args.save,
        teacher_epochs=(args.teacher_epochs or args.epochs),
        student_epochs=(args.student_epochs or args.epochs),
        device=device,
        save_each_epoch=args.save_each_epoch,
        log_gate_stats=args.log_gate_stats,
        model_config=model_config,
        training_mode=args.ablation,
        distill_weight=args.distill_weight,
        stage1_checkpoint=args.stage1_checkpoint,
        seed=args.seed,
    )
    history_stem, _ = os.path.splitext(args.save)
    history_path = f"{history_stem}_history.json"
    with open(history_path, "w", encoding="utf-8") as handle:
        json.dump(history, handle, ensure_ascii=False, indent=2)
    print(f"训练历史 -> {history_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Gated Latent Cross-Attention Teacher + source-only Student "
            "two-stage password model"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="cmd", required=True)
    train_parser = subparsers.add_parser("train", help="训练模型")
    train_parser.add_argument(
        "--data", default=os.path.join(SCRIPT_DIR, "data", "gmail_train.jsonl")
    )
    train_parser.add_argument(
        "--save", default=os.path.join(SCRIPT_DIR, "models", "best_model_glca.pt")
    )
    train_parser.add_argument("--epochs", type=int, default=NUM_EPOCHS)
    train_parser.add_argument("--teacher-epochs", type=int, default=None)
    train_parser.add_argument("--student-epochs", type=int, default=None)
    train_parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    train_parser.add_argument(
        "--sim-threshold", type=float, default=COSINE_SIM_THRESHOLD
    )
    train_parser.add_argument(
        "--valid-ratio", type=float, default=VALID_RATIO
    )
    train_parser.add_argument("--max-pairs", type=int, default=None)
    train_parser.add_argument("--workers", type=int, default=0)
    train_parser.add_argument("--device", default=None)
    train_parser.add_argument(
        "--cross-latent-dim",
        type=int,
        default=DEFAULT_CROSS_LATENT_DIM,
        help="GLCA 的压缩 Q/K/V 总维度；必须可被 attention heads 整除",
    )
    train_parser.add_argument(
        "--teacher-layers",
        type=int,
        default=NUM_HGT_LAYERS,
        help="共享 self-encoding + 双向 GLCA block 数",
    )
    train_parser.add_argument(
        "--ablation",
        choices=("full", "warm_start_lambda0", "student_only"),
        default="full",
    )
    train_parser.add_argument("--distill-weight", type=float, default=None)
    train_parser.add_argument("--stage1-checkpoint", default=None)
    train_parser.add_argument("--seed", type=int, default=42)
    train_parser.add_argument(
        "--no-view-embed",
        action="store_true",
        help="关闭 src/tgt 角色嵌入（不影响无人工边设定）",
    )
    train_parser.add_argument("--save-each-epoch", action="store_true")
    train_parser.add_argument(
        "--log-gate-stats",
        action="store_true",
        help="每轮打印 src<-tgt 与 tgt<-src 的平均注入门值",
    )
    return parser


if __name__ == "__main__":
    parsed_args = build_parser().parse_args()
    cmd_train(parsed_args)
