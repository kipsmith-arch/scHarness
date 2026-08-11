"""D-1 真值规范化:dataset/index CSV -> experiments/gt_cells.csv。

依据 design/experiment_implementation.md §1.2 D-1:
- 从 ``dataset/index/SRP171040.h5ad.csv`` 读入,index 即细胞条码,``Celltype`` 列即真值类型
- 校验:条码数 == 33,956;与 h5ad ``obs_names`` 100% 对齐
- 输出 ``experiments/gt_cells.csv``(列:``cell_barcode``, ``true_type``)

评估脚本(evaluate_annotations.py)以本文件为真值基准。本脚本是 P4 D-1 的可复现实现,
不在 harness / skill 包内,仅供实验层使用。
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

DEFAULT_INDEX_CSV = "dataset/index/SRP171040.h5ad.csv"
DEFAULT_H5AD = "dataset/h5ad/SRP171040.h5ad"
DEFAULT_OUT = "experiments/gt_cells.csv"
EXPECTED_N = 33956


def read_index_rows(index_csv: str) -> list[tuple[str, str]]:
    """Return [(cell_barcode, true_type), ...] from the index CSV.

    Layout: header line starts with empty column name, then Seurat_clusters, Celltype.
    Index column = barcode; third column = Celltype.
    """
    rows: list[tuple[str, str]] = []
    with open(index_csv, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None:
            raise SystemExit(f"error: {index_csv} 为空")
        for lineno, r in enumerate(reader, start=2):
            if not r or not r[0].strip():
                continue
            if len(r) < 3:
                raise SystemExit(
                    f"error: {index_csv} 第 {lineno} 行列数不足({len(r)}),期望 >= 3"
                )
            barcode = r[0].strip()
            true_type = r[2].strip()
            if not true_type:
                raise SystemExit(f"error: {index_csv} 第 {lineno} 行 Celltype 为空")
            rows.append((barcode, true_type))
    return rows


def check_h5ad_alignment(h5ad_path: str, barcodes: set[str]) -> None:
    """Verify gt barcodes == h5ad obs_names (100% alignment, one load)."""
    try:
        import anndata as ad
    except ImportError:
        print("[build_gt_cells] WARNING: anndata 不可用,跳过 h5ad 对齐校验", file=sys.stderr)
        return
    adata = ad.read_h5ad(h5ad_path, backed="r")
    obs_names = set(adata.obs_names)
    n_obs = len(obs_names)
    missing = barcodes - obs_names
    extra = obs_names - barcodes
    if n_obs != len(barcodes) or missing or extra:
        raise SystemExit(
            f"error: h5ad obs_names 与 index 条码未对齐 "
            f"(n_obs={n_obs}, n_gt={len(barcodes)}, 缺失={len(missing)}, 多余={len(extra)})"
        )
    print(f"[build_gt_cells] h5ad obs_names 对齐校验通过(n_obs={n_obs})")


def main() -> int:
    ap = argparse.ArgumentParser(description="D-1 真值规范化 -> experiments/gt_cells.csv")
    ap.add_argument("--index-csv", default=DEFAULT_INDEX_CSV)
    ap.add_argument("--h5ad", default=DEFAULT_H5AD)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--expected-n", type=int, default=EXPECTED_N)
    args = ap.parse_args()

    rows = read_index_rows(args.index_csv)
    if len(rows) != args.expected_n:
        raise SystemExit(
            f"error: 条码数 {len(rows)} != 期望 {args.expected_n};不产出文件"
        )

    barcodes = {b for b, _ in rows}
    check_h5ad_alignment(args.h5ad, barcodes)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    types = sorted({t for _, t in rows})
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["cell_barcode", "true_type"])
        writer.writerows(rows)

    print(f"[build_gt_cells] 写出 {args.out}:{len(rows)} 条码,{len(types)} 个真值类型")
    return 0


if __name__ == "__main__":
    sys.exit(main())
