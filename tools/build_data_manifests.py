#!/usr/bin/env python3
"""Build reproducible line-reference manifests without copying password data."""

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple


SEED = 20260922


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_digest(*parts: str) -> bytes:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.digest()


def valid_ascii(value: object, minimum: int) -> bool:
    return isinstance(value, str) and minimum <= len(value) <= 20 and all(
        32 <= ord(character) <= 126 for character in value
    )


def write_manifest(path: Path, content: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(content, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def build_rankguess_manifest(benchmark_root: Path, output_path: Path) -> None:
    test_root = benchmark_root / "test"
    source_names = {
        "csdn": "test_csdn.txt",
        "rockyou": "testword_rockyou.txt",
        "netease": "testword_netease.txt",
        "000webhost": "testword_000webhost.txt",
    }
    training_path = benchmark_root / "180w" / "train_csdn.txt"
    training_values: Set[str] = set()
    with training_path.open("r", encoding="utf-8-sig", errors="strict") as stream:
        for raw_line in stream:
            value = raw_line.rstrip("\r\n")
            if valid_ascii(value, 5):
                training_values.add(value)

    samples: List[Dict[str, object]] = []
    files: Dict[str, Dict[str, object]] = {}
    statistics: Dict[str, Dict[str, int]] = {}
    selected_values = {"calibration": set(), "test": set()}
    for label, filename in source_names.items():
        path = test_root / filename
        buckets = {"calibration": [], "test": []}
        seen: Set[str] = set()
        stats = defaultdict(int)
        with path.open("r", encoding="utf-8-sig", errors="strict") as stream:
            for line_number, raw_line in enumerate(stream, 1):
                stats["rows"] += 1
                value = raw_line.rstrip("\r\n")
                if not valid_ascii(value, 5):
                    stats["out_of_domain"] += 1
                    continue
                if value in training_values:
                    stats["training_overlap"] += 1
                    continue
                if value in seen:
                    stats["duplicate_in_source"] += 1
                    continue
                seen.add(value)
                partition = "calibration" if stable_digest(str(SEED), value)[0] % 2 == 0 else "test"
                score = stable_digest(str(SEED), label, value)
                buckets[partition].append((score, line_number, value))
        for partition in ("calibration", "test"):
            chosen = sorted(buckets[partition], key=lambda item: item[0])[:1500]
            if len(chosen) != 1500:
                raise RuntimeError("%s has fewer than 1500 eligible %s rows" % (label, partition))
            for _, line_number, value in chosen:
                samples.append({"source": label, "line": line_number, "partition": partition})
                selected_values[partition].add(value)
            stats["selected_%s" % partition] = len(chosen)
        files[label] = {
            "path": str(path),
            "sha256": sha256_file(path),
        }
        statistics[label] = dict(stats)

    overlap = selected_values["calibration"] & selected_values["test"]
    if overlap:
        raise RuntimeError("RankGuess input values crossed partitions")
    write_manifest(
        output_path,
        {
            "schema": 1,
            "kind": "rankguess-line-references",
            "seed": SEED,
            "contains_passwords": False,
            "files": files,
            "training_exclusion": {
                "path": str(training_path),
                "sha256": sha256_file(training_path),
            },
            "statistics": statistics,
            "samples": samples,
            "validation": {
                "sample_count": len(samples),
                "shared_values_between_partitions": 0,
            },
        },
    )


class UnionFind:
    def __init__(self) -> None:
        self.parent: Dict[str, str] = {}

    def find(self, value: str) -> str:
        self.parent.setdefault(value, value)
        if self.parent[value] != value:
            self.parent[value] = self.find(self.parent[value])
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            smaller, larger = sorted((left_root, right_root))
            self.parent[larger] = smaller


def parse_pair_line(raw_line: str) -> Optional[Tuple[str, str]]:
    try:
        record = json.loads(raw_line)
    except json.JSONDecodeError:
        return None
    if isinstance(record, dict):
        source, target = record.get("src"), record.get("tgt")
    elif isinstance(record, list) and len(record) >= 2:
        source, target = record[:2]
    else:
        return None
    if not valid_ascii(source, 1) or not valid_ascii(target, 1):
        return None
    return source, target


def build_pard_manifest(data_root: Path, output_path: Path) -> None:
    source_names = {
        "csdn": "csdn_test.jsonl",
        "dodonew": "dodonew_test.jsonl",
        "000webhost_to_clixsense": "xsite__000webhost_2_clixsense_test.jsonl",
    }
    union_find = UnionFind()
    rows: List[Tuple[str, int, str, str]] = []
    files: Dict[str, Dict[str, object]] = {}
    statistics: Dict[str, Dict[str, int]] = {}
    candidate_pairs: Set[Tuple[str, str]] = set()

    for label, filename in source_names.items():
        path = data_root / filename
        seen: Set[Tuple[str, str]] = set()
        stats = defaultdict(int)
        with path.open("r", encoding="utf-8-sig", errors="strict") as stream:
            for line_number, raw_line in enumerate(stream, 1):
                stats["rows"] += 1
                pair = parse_pair_line(raw_line)
                if pair is None:
                    stats["out_of_domain_or_invalid"] += 1
                    continue
                if pair in seen:
                    stats["duplicate_in_source"] += 1
                    continue
                seen.add(pair)
                source, target = pair
                union_find.union(source, target)
                rows.append((label, line_number, source, target))
                candidate_pairs.add(pair)
        files[label] = {"path": str(path), "sha256": sha256_file(path)}
        statistics[label] = dict(stats)

    training_files = sorted(data_root.glob("*_train.jsonl"))
    training_overlap: Set[Tuple[str, str]] = set()
    training_metadata = []
    for path in training_files:
        with path.open("r", encoding="utf-8-sig", errors="strict") as stream:
            for raw_line in stream:
                pair = parse_pair_line(raw_line)
                if pair in candidate_pairs:
                    training_overlap.add(pair)
        training_metadata.append({"path": str(path), "sha256": sha256_file(path)})

    buckets: Dict[Tuple[str, str], List[Tuple[bytes, int, str, str]]] = defaultdict(list)
    for label, line_number, source, target in rows:
        if (source, target) in training_overlap:
            statistics[label]["training_overlap"] = statistics[label].get("training_overlap", 0) + 1
            continue
        root = union_find.find(source)
        partition = "calibration" if stable_digest(str(SEED), root)[0] % 2 == 0 else "test"
        score = stable_digest(str(SEED), label, source, target)
        buckets[(label, partition)].append((score, line_number, source, target))

    samples: List[Dict[str, object]] = []
    partition_values = {"calibration": set(), "test": set()}
    for label in source_names:
        for partition in ("calibration", "test"):
            chosen = sorted(buckets[(label, partition)], key=lambda item: item[0])[:500]
            if len(chosen) != 500:
                raise RuntimeError("%s has fewer than 500 eligible %s pairs" % (label, partition))
            for _, line_number, source, target in chosen:
                samples.append({"source": label, "line": line_number, "partition": partition})
                partition_values[partition].update((source, target))
            statistics[label]["selected_%s" % partition] = len(chosen)

    overlap = partition_values["calibration"] & partition_values["test"]
    if overlap:
        raise RuntimeError("PARD input values crossed partitions")
    write_manifest(
        output_path,
        {
            "schema": 1,
            "kind": "pard-pair-line-references",
            "seed": SEED,
            "contains_passwords": False,
            "files": files,
            "training_files_checked": training_metadata,
            "statistics": statistics,
            "samples": samples,
            "validation": {
                "sample_count": len(samples),
                "connected_components": len({union_find.find(value) for value in union_find.parent}),
                "shared_values_between_partitions": 0,
                "exact_training_pairs_excluded": len(training_overlap),
                "limitation": "No user identifier is available; partition isolation is based on connected password values.",
            },
        },
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--benchmark-root",
        type=Path,
        default=Path(r"D:\研究生\笔记\benchmark\benchmark"),
    )
    parser.add_argument("--pard-root", type=Path, default=Path(r"D:\研究生\HGN\data"))
    parser.add_argument("--output-root", type=Path, default=Path("data/manifests"))
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    build_rankguess_manifest(arguments.benchmark_root, arguments.output_root / "rankguess.json")
    build_pard_manifest(arguments.pard_root, arguments.output_root / "pard.json")
    print(json.dumps({"status": "OK", "output_root": str(arguments.output_root)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
