#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SANY_ELE_RANK 数据预处理与评分算法模块
从 debug_utils/test_rank.py 提取，配置化常量。
"""

import configparser
import math
import os
from datetime import datetime, timedelta

# 读取算法配置
_config = configparser.ConfigParser()
_config.read(os.path.join(os.path.dirname(__file__), 'config', 'algorithm.ini'))

# 解析配置项
TARGET_HOUR_OFFSETS = [int(x.strip()) for x in _config.get('time', 'target_hour_offsets').split(',')]
ALIGN_THRESHOLD_SEC = int(_config.get('time', 'align_threshold_sec'))
SCORE_MULTIPLIER = float(_config.get('score', 'score_multiplier'))
SCORE_METHOD = _config.get('score', 'score_method', fallback='spike').strip().lower()
RISING_EDGE_MU_FLOOR = float(_config.get('score', 'rising_edge_mu_floor', fallback='0.1'))
DEVICE_NAME_PATTERN = _config.get('filter', 'device_name_pattern')
_exclude_raw = _config.get('filter', 'exclude_buildings', fallback='')
EXCLUDE_BUILDINGS = [b.strip() for b in _exclude_raw.split(',') if b.strip()]

# 时段标签
TIME_LABELS = ["23-00", "00-01", "01-02", "02-03", "03-04", "04-05", "05-06"]


def build_target_hours(night_date):
    """构建目标整点的 datetime 列表"""
    base = datetime(night_date.year, night_date.month, night_date.day, 0, 0)
    return [base + timedelta(hours=h) for h in TARGET_HOUR_OFFSETS]


def deduplicate(readings):
    """
    去重：连续相同 total_reading 的记录，只保留一条。
    保留规则：每组中取 read_time 最早的那条。
    """
    if not readings:
        return []

    result = []
    group_reading = readings[0][1]
    group_first = readings[0]

    for i in range(1, len(readings)):
        rt, val = readings[i]
        if val == group_reading:
            continue
        else:
            result.append(group_first)
            group_reading = val
            group_first = readings[i]

    result.append(group_first)
    return result


def align_to_hours(readings, target_hours):
    """
    将去重后的记录映射到最近的目标整点。
    每条记录映射到时间差最小的整点（不超过阈值），同一整点取时间差最小的。
    返回 {整点索引: total_reading}
    """
    aligned = {}
    aligned_diff = {}

    for read_time, total_reading in readings:
        best_idx = None
        best_diff = float("inf")

        for idx, th in enumerate(target_hours):
            diff = abs((read_time - th).total_seconds())
            if diff < best_diff:
                best_diff = diff
                best_idx = idx

        if best_diff > ALIGN_THRESHOLD_SEC:
            continue

        if best_idx not in aligned or best_diff < aligned_diff[best_idx]:
            aligned[best_idx] = total_reading
            aligned_diff[best_idx] = best_diff

    return aligned


def interpolate_readings(aligned, num_points=9):
    """
    对目标整点的 total_reading 进行线性插值。
    返回 (长度为 num_points 的列表, stable_data 标志)。
    """
    known = sorted(aligned.items())
    if len(known) < 2:
        return None, False

    result = [None] * num_points

    for idx, val in known:
        result[idx] = val

    for i in range(num_points):
        if result[i] is not None:
            continue

        before_idx, before_val = None, None
        for idx, val in known:
            if idx < i:
                before_idx, before_val = idx, val
            else:
                break

        after_idx, after_val = None, None
        for idx, val in known:
            if idx > i:
                after_idx, after_val = idx, val
                break

        if before_idx is not None and after_idx is not None:
            ratio = (i - before_idx) / (after_idx - before_idx)
            result[i] = before_val + (after_val - before_val) * ratio
        elif before_idx is not None:
            result[i] = before_val
        elif after_idx is not None:
            result[i] = after_val

    # stable_data：索引 1~8（23:00~06:00）全部有直接数据
    stable = all(i in aligned for i in range(1, 9))

    return result, stable


def compute_hourly_usage(readings_9):
    """
    用相邻整点的 total_reading 差值计算 7 个小时用电量。
    readings_9[1]=23:00, readings_9[2]=00:00, ..., readings_9[8]=06:00
    """
    usage = []
    for i in range(1, 8):
        diff = readings_9[i + 1] - readings_9[i]
        if diff < 0:
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


def _calculate_score_spike(x):
    """评分算法A：邻居突出度（spike）"""
    spikes = calculate_spikes(x)
    s = sum(spikes)
    return min(100.0, s * SCORE_MULTIPLIER)


def _calculate_score_rising_edge(x):
    """
    评分算法B：上升沿检测（rising_edge）
    聚焦相邻时段的正向增量，归一化后平方和，指数饱和映射到 0~100。
    """
    mu = sum(x) / len(x)
    mu = max(mu, RISING_EDGE_MU_FLOOR)

    s = 0.0
    for i in range(len(x) - 1):
        r = max(0.0, x[i + 1] - x[i])
        s += (r / mu) ** 2

    return 100.0 * (1.0 - math.exp(-s))


def calculate_score(x):
    """根据配置选择评分算法"""
    if SCORE_METHOD == 'rising_edge':
        return _calculate_score_rising_edge(x)
    else:
        return _calculate_score_spike(x)
