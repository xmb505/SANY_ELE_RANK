#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import configparser
import pymysql
import threading
import traceback
import os
import re
from queue import Queue
from contextlib import contextmanager
from collections import defaultdict
from datetime import datetime, timedelta

# 读取配置文件
config = configparser.ConfigParser()
config.read(os.path.join(os.path.dirname(__file__), 'config', 'server.ini'))

# 数据库配置
DB_HOST = config.get('mysql', 'mysql_server')
DB_PORT = int(config.get('mysql', 'mysql_port'))
DB_USER = config.get('mysql', 'login_user')
DB_PASSWORD = config.get('mysql', 'login_passwd')
DB_NAME = config.get('mysql', 'db_schema')

# 连接池设置
CONNECTION_POOL_SIZE = int(config.get('mysql', 'connection_pool_size', fallback='10'))
connection_pool = Queue(maxsize=CONNECTION_POOL_SIZE)
connection_lock = threading.Lock()


class DatabaseManager:
    @staticmethod
    def create_connection():
        """创建一个新的数据库连接"""
        return pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            charset='utf8mb4',
            connect_timeout=5,
            read_timeout=30,
            write_timeout=30,
            autocommit=True
        )

    @staticmethod
    def initialize_connection_pool():
        """初始化数据库连接池"""
        print(f"[INFO] 初始化数据库连接池，大小: {CONNECTION_POOL_SIZE}")
        for _ in range(CONNECTION_POOL_SIZE):
            try:
                conn = DatabaseManager.create_connection()
                connection_pool.put(conn)
            except Exception as e:
                print(f"[ERROR] 创建连接池失败: {str(e)}")
                traceback.print_exc()

    @staticmethod
    def close_all_connections():
        """关闭所有数据库连接"""
        print("[INFO] 关闭所有数据库连接")
        with connection_lock:
            while not connection_pool.empty():
                try:
                    conn = connection_pool.get_nowait()
                    conn.close()
                except:
                    pass

    @staticmethod
    @contextmanager
    def get_connection():
        """获取数据库连接的上下文管理器"""
        conn = None
        try:
            conn = connection_pool.get(timeout=2)
            print("[INFO] 从连接池获取数据库连接")
            conn.ping(reconnect=True)
        except:
            print("[WARN] 连接池获取连接超时或连接无效，创建新连接")
            conn = DatabaseManager.create_connection()

        try:
            yield conn
        finally:
            DatabaseManager.release_connection(conn)

    @staticmethod
    def release_connection(conn):
        """释放数据库连接"""
        if conn is None:
            return
        try:
            conn.ping(reconnect=True)
            if not connection_pool.full():
                connection_pool.put_nowait(conn)
                print("[INFO] 数据库连接已返回连接池")
            else:
                conn.close()
                print("[INFO] 连接池已满，直接关闭连接")
        except Exception as e:
            print(f"[WARN] 连接无效，丢弃: {str(e)}")
            try:
                conn.close()
            except:
                pass


def _build_exclude_clause(exclude_list, params, table_alias='d'):
    """构建楼栋排除的 SQL 片段"""
    if not exclude_list:
        return ''
    placeholders = ','.join(['%s'] * len(exclude_list))
    clause = f" AND REGEXP_SUBSTR({table_alias}.equipmentName, '[0-9]+栋') NOT IN ({placeholders})"
    params.extend(exclude_list)
    return clause


class DataQuery:
    @staticmethod
    def fetch_devices(name_pattern):
        """获取学生宿舍电表设备列表"""
        sql = """
            SELECT id, equipmentName, installationSite
            FROM device
            WHERE equipmentType = 0
              AND equipmentName LIKE %s
        """
        with DatabaseManager.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, (name_pattern,))
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

    @staticmethod
    def fetch_night_readings(device_ids, night_date):
        """
        获取所有设备在指定夜晚的原始读数。
        查询窗口：night_date 前一天 21:30 ~ night_date 06:30。
        返回 {device_id: [(read_time, total_reading), ...]}，按 read_time 升序。
        """
        if not device_ids:
            return {}

        base = datetime(night_date.year, night_date.month, night_date.day, 0, 0)
        query_start = base - timedelta(hours=2, minutes=30)
        query_end = base + timedelta(hours=6, minutes=30)

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

        with DatabaseManager.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                rows = cursor.fetchall()

        readings = defaultdict(list)
        for device_id, read_time, total_reading in rows:
            readings[device_id].append((read_time, float(total_reading)))

        total_records = sum(len(v) for v in readings.values())
        print(f"[INFO] 查询到 {total_records} 条原始读数，覆盖 {len(readings)} 个设备")
        return readings

    @staticmethod
    def check_night_usage_exists(night_date):
        """检查指定 night_date 是否已有数据"""
        sql = "SELECT COUNT(*) FROM night_usage WHERE night_date = %s"
        with DatabaseManager.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, (night_date,))
                count = cursor.fetchone()[0]
        return count > 0

    @staticmethod
    def get_rank_data(night_date, building="全部", page=1, page_size=20, ratio=None, exclude_buildings=None):
        """获取排名列表数据。ratio 为 1~100 时按百分比返回前 N%，否则按分页返回。"""
        # 基础查询
        base_sql = """
            FROM night_usage nu
            JOIN device d ON nu.device_id = d.id
            WHERE nu.night_date = %s
              AND nu.ele_score IS NOT NULL
        """
        params = [night_date]

        # 楼栋过滤
        if building and building != "全部":
            base_sql += " AND d.equipmentName LIKE %s"
            params.append(f"%{building}%")
        elif exclude_buildings:
            base_sql += _build_exclude_clause(exclude_buildings, params)

        # 查总数
        count_sql = "SELECT COUNT(*) " + base_sql
        with DatabaseManager.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(count_sql, params)
                total = cursor.fetchone()[0]

        # 按百分比或分页计算 LIMIT / OFFSET
        if ratio is not None and 1 <= ratio <= 100:
            from math import ceil
            limit = max(1, ceil(total * ratio / 100))
            offset = 0
        else:
            limit = page_size
            offset = (page - 1) * page_size

        # 查数据
        data_sql = """
            SELECT nu.score_rank, nu.device_id, d.equipmentName, d.installationSite,
                   nu.ele_score, nu.stable_data,
                   nu.n1_use_ele, nu.n2_use_ele, nu.n3_use_ele, nu.n4_use_ele,
                   nu.n5_use_ele, nu.n6_use_ele, nu.n7_use_ele
        """ + base_sql + " ORDER BY nu.score_rank ASC LIMIT %s OFFSET %s"

        data_params = params + [limit, offset]

        with DatabaseManager.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(data_sql, data_params)
                rows = cursor.fetchall()

        result_rows = []
        for row in rows:
            result_rows.append({
                "score_rank": row[0],
                "device_id": str(row[1]),
                "equipmentName": row[2] or "",
                "installationSite": row[3] or "",
                "ele_score": float(row[4]) if row[4] is not None else 0,
                "stable_data": row[5],
                "n1_use_ele": float(row[6]) if row[6] is not None else 0,
                "n2_use_ele": float(row[7]) if row[7] is not None else 0,
                "n3_use_ele": float(row[8]) if row[8] is not None else 0,
                "n4_use_ele": float(row[9]) if row[9] is not None else 0,
                "n5_use_ele": float(row[10]) if row[10] is not None else 0,
                "n6_use_ele": float(row[11]) if row[11] is not None else 0,
                "n7_use_ele": float(row[12]) if row[12] is not None else 0,
            })

        # 统计数据（与数据查询使用相同的过滤条件）
        stats_sql = """
            SELECT COUNT(*), AVG(nu.ele_score), MAX(nu.ele_score),
                   SUM(CASE WHEN nu.ele_score >= 60 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN nu.ele_score >= 30 THEN 1 ELSE 0 END)
        """ + base_sql
        with DatabaseManager.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(stats_sql, params)
                s = cursor.fetchone()

        stats = {
            "valid_count": s[0] or 0,
            "avg_score": round(float(s[1]), 2) if s[1] is not None else 0,
            "max_score": float(s[2]) if s[2] is not None else 0,
            "count_ge60": int(s[3] or 0),
            "count_ge30": int(s[4] or 0),
        }

        return total, result_rows, stats

    @staticmethod
    def get_device_detail(device_id, days=7, night_date=None):
        """获取某设备最近N天的夜间用电记录。若指定 night_date 则返回该日期及之前的记录。"""
        # 设备信息
        device_sql = "SELECT equipmentName, installationSite FROM device WHERE id = %s"
        with DatabaseManager.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(device_sql, (device_id,))
                device_info = cursor.fetchone()

        if not device_info:
            return None

        # 历史记录
        if night_date:
            data_sql = """
                SELECT night_date, n1_use_ele, n2_use_ele, n3_use_ele, n4_use_ele,
                       n5_use_ele, n6_use_ele, n7_use_ele, ele_score, score_rank, stable_data
                FROM night_usage
                WHERE device_id = %s AND night_date <= %s
                ORDER BY night_date DESC
                LIMIT %s
            """
            data_params = (device_id, night_date, days)
        else:
            data_sql = """
                SELECT night_date, n1_use_ele, n2_use_ele, n3_use_ele, n4_use_ele,
                       n5_use_ele, n6_use_ele, n7_use_ele, ele_score, score_rank, stable_data
                FROM night_usage
                WHERE device_id = %s
                ORDER BY night_date DESC
                LIMIT %s
            """
            data_params = (device_id, days)
        with DatabaseManager.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(data_sql, data_params)
                rows = cursor.fetchall()

        records = []
        for row in rows:
            nd = row[0]
            prev_day = nd - timedelta(days=1)
            records.append({
                "night_date": str(nd),
                "date_range": f"{prev_day.strftime('%m-%d')} 23:00 ~ {nd.strftime('%m-%d')} 06:00",
                "n1_use_ele": float(row[1]) if row[1] is not None else 0,
                "n2_use_ele": float(row[2]) if row[2] is not None else 0,
                "n3_use_ele": float(row[3]) if row[3] is not None else 0,
                "n4_use_ele": float(row[4]) if row[4] is not None else 0,
                "n5_use_ele": float(row[5]) if row[5] is not None else 0,
                "n6_use_ele": float(row[6]) if row[6] is not None else 0,
                "n7_use_ele": float(row[7]) if row[7] is not None else 0,
                "ele_score": float(row[8]) if row[8] is not None else 0,
                "score_rank": row[9],
                "stable_data": row[10],
            })

        return {
            "equipmentName": device_info[0] or "",
            "installationSite": device_info[1] or "",
            "records": records,
        }

    @staticmethod
    def get_overview_data(night_date, exclude_buildings=None):
        """获取概览统计数据"""
        # 总体统计（JOIN device 以支持楼栋排除）
        stats_sql = """
            SELECT COUNT(*), AVG(nu.ele_score), MAX(nu.ele_score),
                   SUM(CASE WHEN nu.ele_score >= 60 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN nu.ele_score >= 30 THEN 1 ELSE 0 END)
            FROM night_usage nu
            JOIN device d ON nu.device_id = d.id
            WHERE nu.night_date = %s AND nu.ele_score IS NOT NULL
        """
        stats_params = [night_date]
        if exclude_buildings:
            stats_sql += _build_exclude_clause(exclude_buildings, stats_params)
        with DatabaseManager.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(stats_sql, stats_params)
                s = cursor.fetchone()

        # 总电表设备数
        total_sql = """
            SELECT COUNT(*) FROM device
            WHERE equipmentType = 0 AND equipmentName LIKE %s
        """
        total_params = ["%室电表%"]
        if exclude_buildings:
            placeholders = ','.join(['%s'] * len(exclude_buildings))
            total_sql += f" AND REGEXP_SUBSTR(equipmentName, '[0-9]+栋') NOT IN ({placeholders})"
            total_params.extend(exclude_buildings)
        with DatabaseManager.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(total_sql, total_params)
                total_devices = cursor.fetchone()[0]

        valid_count = s[0] or 0

        # 按楼栋分组统计
        building_sql = """
            SELECT
                REGEXP_SUBSTR(d.equipmentName, '[0-9]+栋') AS building,
                COUNT(*) AS cnt,
                ROUND(AVG(nu.ele_score), 2) AS avg_score,
                SUM(CASE WHEN nu.ele_score >= 60 THEN 1 ELSE 0 END) AS cnt_ge60
            FROM night_usage nu
            JOIN device d ON nu.device_id = d.id
            WHERE nu.night_date = %s AND nu.ele_score IS NOT NULL
        """
        building_params = [night_date]
        if exclude_buildings:
            building_sql += _build_exclude_clause(exclude_buildings, building_params)
        building_sql += """
            GROUP BY building
            HAVING building IS NOT NULL
            ORDER BY avg_score DESC
        """
        with DatabaseManager.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(building_sql, building_params)
                b_rows = cursor.fetchall()

        building_stats = []
        for row in b_rows:
            building_stats.append({
                "building": row[0],
                "count": row[1],
                "avg_score": float(row[2]) if row[2] is not None else 0,
                "count_ge60": int(row[3] or 0),
            })

        return {
            "total_devices": total_devices,
            "valid_count": valid_count,
            "skip_count": total_devices - valid_count,
            "avg_score": round(float(s[1]), 2) if s[1] is not None else 0,
            "max_score": float(s[2]) if s[2] is not None else 0,
            "count_ge60": int(s[3] or 0),
            "count_ge30": int(s[4] or 0),
            "building_stats": building_stats,
        }

    @staticmethod
    def get_building_list():
        """获取楼栋列表"""
        sql = """
            SELECT DISTINCT REGEXP_SUBSTR(equipmentName, '[0-9]+栋') AS building
            FROM device
            WHERE equipmentType = 0 AND equipmentName LIKE %s
            HAVING building IS NOT NULL
            ORDER BY CAST(REGEXP_SUBSTR(building, '[0-9]+') AS UNSIGNED)
        """
        with DatabaseManager.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, ("%室电表%",))
                rows = cursor.fetchall()
        return [row[0] for row in rows if row[0]]

    @staticmethod
    def batch_insert_night_usage(records):
        """批量写入夜间用电数据"""
        if not records:
            return

        sql = """
            INSERT INTO night_usage
                (device_id, night_date, n1_use_ele, n2_use_ele, n3_use_ele,
                 n4_use_ele, n5_use_ele, n6_use_ele, n7_use_ele,
                 stable_data, ele_score)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                n1_use_ele = VALUES(n1_use_ele),
                n2_use_ele = VALUES(n2_use_ele),
                n3_use_ele = VALUES(n3_use_ele),
                n4_use_ele = VALUES(n4_use_ele),
                n5_use_ele = VALUES(n5_use_ele),
                n6_use_ele = VALUES(n6_use_ele),
                n7_use_ele = VALUES(n7_use_ele),
                stable_data = VALUES(stable_data),
                ele_score = VALUES(ele_score),
                record_time_stamp = CURRENT_TIMESTAMP
        """

        # 每100条分批
        batch_size = 100
        with DatabaseManager.get_connection() as conn:
            with conn.cursor() as cursor:
                for i in range(0, len(records), batch_size):
                    batch = records[i:i + batch_size]
                    values = []
                    for r in batch:
                        values.append((
                            r["device_id"], r["night_date"],
                            r["n1"], r["n2"], r["n3"], r["n4"],
                            r["n5"], r["n6"], r["n7"],
                            r["stable_data"], r["ele_score"],
                        ))
                    cursor.executemany(sql, values)
                    print(f"[INFO] 写入 {len(batch)} 条夜间用电记录")

    @staticmethod
    def update_score_ranks(night_date):
        """按 ele_score 降序回填 score_rank"""
        sql = """
            UPDATE night_usage nu
            JOIN (
                SELECT id, ROW_NUMBER() OVER (ORDER BY ele_score DESC) AS rk
                FROM night_usage
                WHERE night_date = %s AND ele_score IS NOT NULL
            ) ranked ON nu.id = ranked.id
            SET nu.score_rank = ranked.rk
        """
        with DatabaseManager.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, (night_date,))
                affected = cursor.rowcount
        print(f"[INFO] 回填排名完成，更新 {affected} 条记录")
