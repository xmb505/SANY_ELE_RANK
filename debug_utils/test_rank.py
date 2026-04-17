#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SANY_ELE_RANK 夜间用电异常排名调试工具

从 MySQL 数据库读取电表原始数据，执行数据预处理、spike 评分和排名，
在终端打印前 N% 高分宿舍。
"""

import argparse
import sys
from datetime import datetime, date, timedelta
from collections import defaultdict
from math import ceil

import pymysql

# ============ 常量 ============
DB_NAME = "sany_check"
DB_PORT = 3306

# 9 个目标整点的小时偏移（相对于 night_date 00:00）
# 索引 0 = 22:00（前一天），仅作插值锚点
# 索引 1~8 = 23:00, 00:00, ..., 06:00
TARGET_HOUR_OFFSETS = [-2, -1, 0, 1, 2, 3, 4, 5, 6]

# 时段标签
TIME_LABELS = ["23-00", "00-01", "01-02", "02-03", "03-04", "04-05", "05-06"]

# 整点对齐阈值（秒）
ALIGN_THRESHOLD_SEC = 30 * 60  # 30 分钟

# 评分映射系数
SCORE_MULTIPLIER = 25.0


# ============ 参数解析 ============

def parse_args():
    parser = argparse.ArgumentParser(
        description="SANY_ELE_RANK 夜间用电异常排名调试工具"
    )
    parser.add_argument("--database_ip", required=True, help="MySQL 服务器地址")
    parser.add_argument("--database_account", required=True, help="数据库用户名")
    parser.add_argument("--database_password", required=True, help="数据库密码")
    parser.add_argument(
        "--night_date", required=True, type=_parse_date,
        help="夜晚归属日期（凌晨侧），格式 YYYY-MM-DD，如 2026-04-17 表示 4/16 23:00 ~ 4/17 06:00"
    )
    parser.add_argument(
        "--score_rank_ratio", required=True, type=int,
        help="打印前 N%% 高分设备（1~100）"
    )
    parser.add_argument(
        "--exclude", nargs="*", default=[],
        help="排除指定楼栋（如 --exclude 6栋 7栋），匹配 equipmentName 中包含该关键词的设备"
    )
    args = parser.parse_args()

    if not 1 <= args.score_rank_ratio <= 100:
        parser.error("--score_rank_ratio 必须在 1~100 之间")

    return args


def _parse_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"日期格式错误: '{s}'，请使用 YYYY-MM-DD")


# ============ 数据库连接 ============

def create_connection(args):
    try:
        conn = pymysql.connect(
            host=args.database_ip,
            port=DB_PORT,
            user=args.database_account,
            password=args.database_password,
            database=DB_NAME,
            charset="utf8mb4",
            connect_timeout=5,
            read_timeout=30,
        )
        print(f"[INFO] 已连接数据库 {args.database_ip}:{DB_PORT}/{DB_NAME}")
        return conn
    except Exception as e:
        print(f"[ERROR] 数据库连接失败: {e}", file=sys.stderr)
        sys.exit(1)


# ============ 数据获取 ============

def fetch_devices(conn):
    """获取学生宿舍电表设备列表"""
    sql = """
        SELECT id, equipmentName, installationSite
        FROM device
        WHERE equipmentType = 0
          AND equipmentName LIKE %s
    """
    with conn.cursor() as cursor:
        cursor.execute(sql, ("%室电表%",))
        rows = cursor.fetchall()

    devices = []
    for row in rows:
        devices.append({
            "id": row[0],
            "name": row[1] or "",
            "site": row[2] or "",
        })

    print(f"[INFO] 查询到 {len(devices)} 个学生宿舍电表设备")
    return devices


def fetch_night_readings(conn, device_ids, night_date):
    """
    获取所有设备在指定夜晚的原始读数。
    查询窗口：night_date 前一天 21:30 ~ night_date 06:30（留 30 分钟缓冲）。
    返回 {device_id: [(read_time, total_reading), ...]}，按 read_time 升序。
    """
    if not device_ids:
        return {}

    base = datetime(night_date.year, night_date.month, night_date.day, 0, 0)
    query_start = base - timedelta(hours=2, minutes=30)  # 前一天 21:30
    query_end = base + timedelta(hours=6, minutes=30)     # 当天 06:30

    placeholders = ",".join(["%s"] * len(device_ids))
    sql = f"""
        SELECT device_id, read_time, total_reading
        FROM data
        WHERE device_id IN ({placeholders})
          AND read_time BETWEEN %s AND %s
          AND unStandard = 0
          AND total_reading IS NOT NULL
        ORDER BY device_id, read_time
    """
    params = list(device_ids) + [query_start, query_end]

    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()

    readings = defaultdict(list)
    for device_id, read_time, total_reading in rows:
        readings[device_id].append((read_time, float(total_reading)))

    total_records = sum(len(v) for v in readings.values())
    print(f"[INFO] 查询到 {total_records} 条原始读数，覆盖 {len(readings)} 个设备")
    return readings


# ============ 数据预处理 ============

def build_target_hours(night_date):
    """构建 9 个目标整点的 datetime 列表"""
    base = datetime(night_date.year, night_date.month, night_date.day, 0, 0)
    return [base + timedelta(hours=h) for h in TARGET_HOUR_OFFSETS]


def deduplicate(readings):
    """
    去重：连续相同 total_reading 的记录，只保留一条。
    保留规则：每组中取 read_time 最早的那条（最接近变化发生的时刻）。
    """
    if not readings:
        return []

    result = []
    group_reading = readings[0][1]
    group_first = readings[0]

    for i in range(1, len(readings)):
        rt, val = readings[i]
        if val == group_reading:
            # 同组，不更新（保留最早的）
            continue
        else:
            # 新组开始，保存上一组的代表
            result.append(group_first)
            group_reading = val
            group_first = readings[i]

    # 保存最后一组
    result.append(group_first)
    return result


def align_to_hours(readings, target_hours):
    """
    将去重后的记录映射到最近的目标整点。
    - 每条记录映射到时间差最小的整点（不超过 30 分钟）
    - 同一整点被多条记录争夺时，取时间差最小的
    返回 {整点索引: total_reading}
    """
    aligned = {}       # {idx: total_reading}
    aligned_diff = {}  # {idx: 最小时间差（秒）}

    for read_time, total_reading in readings:
        best_idx = None
        best_diff = float("inf")

        for idx, th in enumerate(target_hours):
            diff = abs((read_time - th).total_seconds())
            if diff < best_diff:
                best_diff = diff
                best_idx = idx

        if best_diff > ALIGN_THRESHOLD_SEC:
            continue  # 超过阈值，丢弃

        if best_idx not in aligned or best_diff < aligned_diff[best_idx]:
            aligned[best_idx] = total_reading
            aligned_diff[best_idx] = best_diff

    return aligned


def interpolate_readings(aligned, num_points=9):
    """
    对 9 个整点的 total_reading 进行线性插值。
    aligned: {整点索引: total_reading}
    返回长度为 9 的列表，以及 stable_data 标志。
    """
    known = sorted(aligned.items())  # [(idx, val), ...]
    if len(known) < 2:
        return None, False

    result = [None] * num_points

    # 填入已知值
    for idx, val in known:
        result[idx] = val

    # 对缺失值进行线性插值
    for i in range(num_points):
        if result[i] is not None:
            continue

        # 找前一个已知点
        before_idx, before_val = None, None
        for idx, val in known:
            if idx < i:
                before_idx, before_val = idx, val
            else:
                break

        # 找后一个已知点
        after_idx, after_val = None, None
        for idx, val in known:
            if idx > i:
                after_idx, after_val = idx, val
                break

        if before_idx is not None and after_idx is not None:
            # 线性插值
            ratio = (i - before_idx) / (after_idx - before_idx)
            result[i] = before_val + (after_val - before_val) * ratio
        elif before_idx is not None:
            # 尾端外推：取最近已知值
            result[i] = before_val
        elif after_idx is not None:
            # 首端外推：取最近已知值
            result[i] = after_val

    # stable_data：索引 1~8（23:00~06:00）全部有直接数据
    stable = all(i in aligned for i in range(1, 9))

    return result, stable


def compute_hourly_usage(readings_9):
    """
    用相邻整点的 total_reading 差值计算 7 个小时用电量。
    readings_9[1] = 23:00, readings_9[2] = 00:00, ..., readings_9[8] = 06:00
    x[0] = readings_9[2] - readings_9[1]  (23:00 -> 00:00)
    x[6] = readings_9[8] - readings_9[7]  (05:00 -> 06:00)
    """
    usage = []
    for i in range(1, 8):
        diff = readings_9[i + 1] - readings_9[i]
        if diff < 0:
            print(f"  [WARN] 用电量为负 ({TIME_LABELS[i-1]}): {diff:.4f}，强制为 0")
            diff = 0.0
        usage.append(diff)
    return usage


def preprocess_device_readings(readings, target_hours):
    """
    数据预处理总控：去重 -> 对齐 -> 插值 -> 计算用电量。
    返回 (hourly_usage, stable_data) 或 (None, False)。
    """
    deduped = deduplicate(readings)
    if len(deduped) < 2:
        return None, False

    aligned = align_to_hours(deduped, target_hours)
    if len(aligned) < 2:
        return None, False

    readings_9, stable = interpolate_readings(aligned)
    if readings_9 is None or any(v is None for v in readings_9):
        return None, False

    usage = compute_hourly_usage(readings_9)
    return usage, stable


# ============ 评分算法 ============

def calculate_spikes(x):
    """
    计算 7 个 spike 突出度。
    x: 长度为 7 的用电量列表 (x1~x7)
    """
    spikes = [0.0] * 7

    # 边界点
    spikes[0] = max(0.0, x[0] - x[1])
    spikes[6] = max(0.0, x[6] - x[5])

    # 中间点
    for i in range(1, 6):
        spikes[i] = max(0.0, x[i] - (x[i - 1] + x[i + 1]) / 2.0)

    return spikes


def calculate_score(x):
    """计算异常能源使用分数（0~100）"""
    spikes = calculate_spikes(x)
    s = sum(spikes)
    return min(100.0, s * SCORE_MULTIPLIER)


# ============ 排名与输出 ============

def rank_and_print(results, ratio, total_devices, skip_count, night_date):
    """排名并打印前 N% 高分设备"""
    if not results:
        print("\n[INFO] 无有效数据，所有设备均被跳过。")
        return

    # 按分数降序，同分按安装位置排序
    results.sort(key=lambda r: (-r["score"], r["site"]))

    # 计算显示数量
    show_count = max(1, ceil(len(results) * ratio / 100))

    # 全局统计
    all_scores = [r["score"] for r in results]
    all_scores_sorted = sorted(all_scores)
    avg_score = sum(all_scores) / len(all_scores)
    median_score = all_scores_sorted[len(all_scores_sorted) // 2]
    max_score = all_scores_sorted[-1]
    count_ge60 = sum(1 for s in all_scores if s >= 60)
    count_ge30 = sum(1 for s in all_scores if s >= 30)

    # 日期显示
    prev_day = night_date - timedelta(days=1)
    date_range = f"{prev_day} 23:00 ~ {night_date} 06:00"

    # 打印头部
    print()
    print("=" * 100)
    print(f"  SANY_ELE_RANK 夜间用电异常排名")
    print(f"  日期：{date_range}")
    print(f"  电表设备总数：{total_devices} | 有效数据：{len(results)} | 跳过：{skip_count}")
    print(f"  显示前 {ratio}%（共 {show_count} 个）")
    print("=" * 100)

    # 表头
    header = (
        f"{'排名':>4}  "
        f"{'设备名':<20}  "
        f"{'安装位置':<14}  "
        f"{'分数':>6}  "
        + "  ".join(f"{l:>5}" for l in TIME_LABELS)
        + f"  {'稳定':>4}"
    )
    print(header)
    print("─" * 100)

    # 打印前 N%
    for rank_idx, r in enumerate(results[:show_count], 1):
        stable_mark = "Y" if r["stable"] else "N"
        usage_str = "  ".join(f"{u:5.2f}" for u in r["usage"])
        line = (
            f"{rank_idx:>4}  "
            f"{r['name']:<20}  "
            f"{r['site']:<14}  "
            f"{r['score']:>6.2f}  "
            f"{usage_str}"
            f"  {stable_mark:>4}"
        )
        print(line)

    # 尾部统计
    print("─" * 100)
    print(
        f"全部设备统计 - "
        f"平均分：{avg_score:.2f} | "
        f"中位分：{median_score:.2f} | "
        f"最高分：{max_score:.2f} | "
        f">=60分：{count_ge60} | "
        f">=30分：{count_ge30}"
    )
    print()


# ============ 主函数 ============

def main():
    args = parse_args()

    # 连接数据库
    conn = create_connection(args)

    try:
        # 获取设备列表
        devices = fetch_devices(conn)

        # 排除指定楼栋
        if args.exclude:
            before = len(devices)
            devices = [
                d for d in devices
                if not any(kw in d["name"] for kw in args.exclude)
            ]
            excluded = before - len(devices)
            if excluded > 0:
                print(f"[INFO] 已排除 {excluded} 个设备（关键词：{', '.join(args.exclude)}）")

        if not devices:
            print("[ERROR] 未找到符合条件的电表设备", file=sys.stderr)
            sys.exit(1)

        # 获取夜间读数
        device_ids = [d["id"] for d in devices]
        all_readings = fetch_night_readings(conn, device_ids, args.night_date)
    finally:
        conn.close()
        print("[INFO] 数据库连接已关闭")

    # 构建目标整点
    target_hours = build_target_hours(args.night_date)

    # 逐设备处理
    results = []
    skip_count = 0

    for device in devices:
        readings = all_readings.get(device["id"], [])
        if len(readings) < 2:
            skip_count += 1
            continue

        usage, stable = preprocess_device_readings(readings, target_hours)
        if usage is None:
            skip_count += 1
            continue

        score = calculate_score(usage)
        results.append({
            "device_id": device["id"],
            "name": device["name"],
            "site": device["site"],
            "usage": usage,
            "score": score,
            "stable": stable,
        })

    # 排名并打印
    rank_and_print(
        results, args.score_rank_ratio,
        len(devices), skip_count, args.night_date
    )


if __name__ == "__main__":
    main()
