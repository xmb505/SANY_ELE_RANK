#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import configparser
import sys
import os
import re
import traceback
import atexit
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

# 读取配置文件
config = configparser.ConfigParser()
config.read(os.path.join(os.path.dirname(__file__), 'config', 'server.ini'))

# 服务器配置
SERVER_PORT = int(config.get('server', 'port'))

# 创建线程池
executor = ThreadPoolExecutor(max_workers=10)

# 初始化数据库连接池
from database import DatabaseManager
DatabaseManager.initialize_connection_pool()

# 注册退出处理
atexit.register(DatabaseManager.close_all_connections)

# 导入排名处理模块
import ranking


def _parse_exclude(params):
    """解析 exclude 参数，返回楼栋列表或 None"""
    exclude_str = params.get('exclude', [None])[0]
    if not exclude_str:
        return None
    result = []
    for b in exclude_str.split(','):
        b = b.strip()
        if re.match(r'^[0-9]+栋$', b):
            result.append(b)
    return result if result else None


class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # 获取真实客户端IP
        real_ip = self.headers.get('X-Real-IP') or self.headers.get('X-Forwarded-For') or self.client_address[0]
        print(f"[INFO] 收到GET请求 from {real_ip}: {self.path}")
        response_data = {"code": 400, "error": "请求参数错误"}
        try:
            parsed_url = urlparse(self.path)
            params = parse_qs(parsed_url.query)
            print(f"[INFO] 解析参数完成: {params}")

            mode = params.get('mode', [None])[0]
            print(f"[INFO] 请求模式: {mode}")

            if mode == 'rank':
                response_data = self._handle_rank(params)
            elif mode == 'detail':
                response_data = self._handle_detail(params)
            elif mode == 'overview':
                response_data = self._handle_overview(params)
            elif mode == 'buildings':
                response_data = ranking.handle_buildings_request()
            else:
                response_data = {"code": 400, "error": "无效的mode参数"}

            print(f"[INFO] 请求处理完成，响应码: {response_data.get('code', 'N/A')}")
        except Exception as e:
            print(f"[ERROR] 处理请求时出错: {str(e)}")
            traceback.print_exc()
            response_data = {"code": 500, "error": f"服务器内部错误: {str(e)}"}
        finally:
            try:
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()

                response_str = json.dumps(response_data, ensure_ascii=False)
                self.wfile.write(response_str.encode('utf-8'))
                self.wfile.flush()
                print(f"[INFO] 响应发送完成，长度: {len(response_str)}")
            except Exception as e:
                print(f"[ERROR] 发送响应时出错: {str(e)}")

    def _handle_rank(self, params):
        """处理排名请求"""
        night_date_str = params.get('night_date', [None])[0]
        if not night_date_str:
            return {"code": 400, "error": "缺少必要参数 night_date"}

        try:
            night_date = datetime.strptime(night_date_str, '%Y-%m-%d').date()
        except ValueError:
            return {"code": 400, "error": "night_date 格式不正确，应为 YYYY-MM-DD"}

        building = params.get('building', ['全部'])[0]

        # 优先使用 ratio（前百分之几），否则使用分页
        ratio_str = params.get('ratio', [None])[0]
        ratio = None
        if ratio_str is not None:
            try:
                ratio = int(ratio_str)
                if ratio < 1 or ratio > 100:
                    ratio = None
            except ValueError:
                ratio = None

        try:
            page = int(params.get('page', ['1'])[0])
            if page < 1:
                page = 1
        except ValueError:
            page = 1

        try:
            page_size = int(params.get('page_size', ['20'])[0])
            if page_size < 1 or page_size > 100:
                page_size = 20
        except ValueError:
            page_size = 20

        return ranking.handle_rank_request(night_date, building, page, page_size, ratio, _parse_exclude(params))

    def _handle_detail(self, params):
        """处理设备详情请求"""
        device_id = params.get('device_id', [None])[0]
        if not device_id:
            return {"code": 400, "error": "缺少必要参数 device_id"}

        device_id = device_id.strip()
        if not re.match(r'^[a-zA-Z0-9_]+$', device_id) or len(device_id) > 50:
            return {"code": 400, "error": "无效的 device_id 参数"}

        try:
            days = int(params.get('days', ['7'])[0])
            if days < 1 or days > 90:
                days = 7
        except ValueError:
            days = 7

        night_date = None
        night_date_str = params.get('night_date', [None])[0]
        if night_date_str:
            try:
                night_date = datetime.strptime(night_date_str, '%Y-%m-%d').date()
            except ValueError:
                pass

        return ranking.handle_detail_request(device_id, days, night_date)

    def _handle_overview(self, params):
        """处理概览请求"""
        night_date_str = params.get('night_date', [None])[0]
        if not night_date_str:
            return {"code": 400, "error": "缺少必要参数 night_date"}

        try:
            night_date = datetime.strptime(night_date_str, '%Y-%m-%d').date()
        except ValueError:
            return {"code": 400, "error": "night_date 格式不正确，应为 YYYY-MM-DD"}

        return ranking.handle_overview_request(night_date, _parse_exclude(params))


if __name__ == '__main__':
    server = HTTPServer(('', SERVER_PORT), RequestHandler)
    print(f"SANY_ELE_RANK 服务器启动，监听端口 {SERVER_PORT}")
    server.serve_forever()
