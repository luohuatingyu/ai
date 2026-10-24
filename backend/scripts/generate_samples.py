#!/usr/bin/env python3
"""生成内置示例数据集（纯标准库，无需依赖）。

- classification_demo.csv  客户流失二分类
- regression_demo.csv      房价模拟回归
"""
from __future__ import annotations

import csv
import math
import random
from pathlib import Path

import sys

OUT_DIR = Path(__file__).resolve().parent.parent / "sample_data"


def gen_classification(path: Path, n: int = 300, seed: int = 7) -> None:
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        age = rng.randint(18, 70)
        income = rng.randint(2000, 30000)
        visits = rng.randint(0, 40)
        complaints = rng.randint(0, 8)
        vip = rng.choice(["普通", "白银", "黄金"])
        # 真实规律：年龄越大/投诉越多/黄金会员更不易流失
        score = (
            0.045 * (age - 40)
            - 0.8 * complaints
            - 0.05 * (visits - 15)
            + 0.00012 * (income - 10000)
            + (1.1 if vip == "黄金" else 0.3 if vip == "白银" else 0.0)
        )
        p = 1 / (1 + math.exp(-score))
        churn = "是" if rng.random() < p else "否"
        rows.append([age, income, visits, complaints, vip, churn])

    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["年龄", "月收入", "月均访问", "投诉次数", "会员类型", "是否流失"])
        w.writerows(rows)


def gen_regression(path: Path, n: int = 260, seed: int = 11) -> None:
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        area = rng.randint(35, 160)
        rooms = max(1, int(area / 30) + rng.choice([0, 0, 1]))
        floor = rng.randint(1, 30)
        district = rng.choice(["市中心", "近郊", "远郊"])
        base = {"市中心": 3.2, "近郊": 1.8, "远郊": 1.1}[district]
        price = 5.5 * area * base / 10 + 12 * rooms + floor * 1.2 + rng.gauss(0, 18)
        rows.append([area, rooms, floor, district, round(price, 1)])

    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["面积", "房间数", "楼层", "地段", "价格万元"])
        w.writerows(rows)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gen_classification(OUT_DIR / "classification_demo.csv")
    gen_regression(OUT_DIR / "regression_demo.csv")
    print(f"已生成示例数据: {OUT_DIR}")


if __name__ == "__main__":
    sys.exit(main())
