#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SANY_ELE_RANK 排名编排层
核心逻辑：检查数据库是否已有数据 → 无则计算并写入 → 返回结果
"""

import threading
from datetime import timedelta

from database import DataQuery
from algorithm import (
    DEVICE_NAME_PATTERN,
    EXCLUDE_BUILDINGS,
    build_target_hours,
    preprocess_device_readings,
    calculate_score,
)

# 全局锁，防止并发重复计算同一天数据
_compute_lock = threading.Lock()


def ensure_night_data(night_date):
    """
    确保指定 night_date 的数据已计算并存入 night_usage 表。
    如果已有数据则直接返回，否则执行完整计算流程。
    """
    # 先不加锁快速检查
    if DataQuery.check_night_usage_exists(night_date):
        print(f"[INFO] night_date={night_date} 数据已存在，跳过计算")
        return

    with _compute_lock:
        # 加锁后二次检查（可能被其他线程抢先计算了）
        if DataQuery.check_night_usage_exists(night_date):
            print(f"[INFO] night_date={night_date} 数据已存在（二次检查），跳过计算")
            return

        print(f"[INFO] night_date={night_date} 无数据，开始计算...")

        # 步骤A：获取所有电表设备
        devices = DataQuery.fetch_devices(DEVICE_NAME_PATTERN)
        if not devices:
            print("[WARN] 未找到符合条件的电表设备")
            return

        # 步骤B：批量获取原始读数
        device_ids = [d["id"] for d in devices]
        all_readings = DataQuery.fetch_night_readings(device_ids, night_date)

        # 步骤C：逐设备处理
        target_hours = build_target_hours(night_date)
        records = []
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
            records.append({
                "device_id": device["id"],
                "night_date": night_date,
                "n1": round(usage[0], 4),
                "n2": round(usage[1], 4),
                "n3": round(usage[2], 4),
                "n4": round(usage[3], 4),
                "n5": round(usage[4], 4),
                "n6": round(usage[5], 4),
                "n7": round(usage[6], 4),
                "stable_data": 1 if stable else 0,
                "ele_score": round(score, 2),
            })

        print(f"[INFO] 计算完成：有效 {len(records)} 个，跳过 {skip_count} 个")

        if not records:
            print("[WARN] 无有效数据可写入")
            return

        # 步骤D：批量写入（score_rank 为 NULL）
        DataQuery.batch_insert_night_usage(records)

        # 步骤E：统一回填排名
        DataQuery.update_score_ranks(night_date)

        print(f"[INFO] night_date={night_date} 数据计算与写入完成")


def handle_rank_request(night_date, building="全部", page=1, page_size=20, ratio=None, exclude_buildings=None):
    """处理排名列表请求"""
    ensure_night_data(night_date)

    total, rows, stats = DataQuery.get_rank_data(night_date, building, page, page_size, ratio, exclude_buildings)

    prev_day = night_date - timedelta(days=1)
    date_range = f"{prev_day} 23:00 ~ {night_date} 06:00"

    result = {
        "code": 200,
        "night_date": str(night_date),
        "date_range": date_range,
        "building": building,
        "total": total,
        "showing": len(rows),
        "rows": rows,
        "stats": stats,
    }

    if exclude_buildings:
        result["exclude_buildings"] = exclude_buildings

    if ratio is not None:
        result["ratio"] = ratio
    else:
        result["page"] = page
        result["page_size"] = page_size

    return result


def handle_detail_request(device_id, days=7, night_date=None):
    """处理设备详情请求"""
    result = DataQuery.get_device_detail(device_id, days, night_date)
    if result is None:
        return {"code": 404, "error": "设备未找到"}

    return {
        "code": 200,
        "device_id": device_id,
        "equipmentName": result["equipmentName"],
        "installationSite": result["installationSite"],
        "days": days,
        "records": result["records"],
    }


def handle_overview_request(night_date, exclude_buildings=None):
    """处理概览统计请求"""
    ensure_night_data(night_date)

    data = DataQuery.get_overview_data(night_date, exclude_buildings)

    prev_day = night_date - timedelta(days=1)
    date_range = f"{prev_day} 23:00 ~ {night_date} 06:00"

    result = {
        "code": 200,
        "night_date": str(night_date),
        "date_range": date_range,
        **data,
    }
    if exclude_buildings:
        result["exclude_buildings"] = exclude_buildings
    return result


def handle_buildings_request():
    """处理楼栋列表请求"""
    buildings = DataQuery.get_building_list()
    return {
        "code": 200,
        "buildings": buildings,
        "default_exclude": EXCLUDE_BUILDINGS,
    }
