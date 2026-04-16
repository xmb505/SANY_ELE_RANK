# SANY_ELE_RANK 项目文档

## 1. 项目概述

### 1.1 项目定位

SANY_ELE_RANK 是一个面向三一工学院宿舍的**夜间用电异常检测与排名系统**。该系统通过分析各寝室电表的历史用电数据，利用特定算法识别夜间时段（如 22:00 - 06:00）内的突发用电高峰，计算出每个寝室的用电异常值，并进行排名，为宿舍用电管理提供数据支撑。

### 1.2 项目背景

在高校宿舍管理中，夜间大功率违规用电（如使用电热水壶、电吹风、电热毯等）不仅存在安全隐患，还会导致电路过载。传统的人工巡查方式效率低下且覆盖面有限。本项目通过数据驱动的方式，自动识别用电异常行为，辅助宿舍管理人员精准定位问题寝室。

### 1.3 与 SANY_check_money 的关系

本项目与 [SANY_check_money](https://github.com/xmb505/SANY_check_money) 项目紧密联动：

| 项目 | 角色 | 职责 |
|------|------|------|
| **SANY_check_money** | 数据采集层 | 负责从学校系统采集各寝室电表的实时/历史用电数据，存储到 MySQL 数据库 |
| **SANY_ELE_RANK** | 数据分析层 | 读取 SANY_check_money 采集并存储的数据，进行异常检测算法运算和排名 |

两者共享同一个 MySQL 数据库实例，SANY_ELE_RANK 直接消费 SANY_check_money 写入的 `device` 和 `data` 表中的数据。

```
┌─────────────────────┐     ┌──────────────┐     ┌─────────────────────┐
│  学校电费查询系统     │────>│  MySQL 数据库 │<────│  SANY_ELE_RANK      │
│  (sywap.funsine.com)│     │              │     │  (异常检测 & 排名)   │
└─────────────────────┘     │  - device 表  │     └─────────────────────┘
         ^                  │  - data 表    │              │
         │                  │  - email 表   │              v
┌─────────────────────┐     └──────────────┘     ┌─────────────────────┐
│  SANY_check_money   │                          │  排名结果展示        │
│  (数据采集 & 预警)   │                          │  (Web 前端)         │
└─────────────────────┘                          └─────────────────────┘
```

---

## 2. 数据来源

### 2.1 数据库表结构

SANY_ELE_RANK 依赖 SANY_check_money 创建并维护的以下数据库表：

#### device 表（设备信息）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | varchar(32) | 设备唯一标识（主键） |
| `addr` | varchar(20) | 设备地址 |
| `equipmentName` | varchar(100) | 设备名称（如 "XX栋XXX室电表"） |
| `installationSite` | varchar(100) | 安装位置（寝室号） |
| `equipmentType` | tinyint(1) | 设备类型（区分电表/水表） |
| `ratio` | decimal(10,2) | 倍率 |
| `rate` | decimal(10,4) | 费率 |
| `acctId` | varchar(20) | 账户ID |
| `status` | tinyint(1) | 设备状态（1=开, 0=关） |
| `created_at` | datetime | 创建时间 |
| `updated_at` | datetime | 更新时间 |

#### data 表（读数数据）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | bigint(20) | 自增主键 |
| `device_id` | varchar(32) | 关联设备ID（外键 -> device.id） |
| `read_time` | datetime | 读数时间 |
| `total_reading` | decimal(15,2) | 当前总读数（累计用电量） |
| `diff_reading` | decimal(15,2) | 差值读数 |
| `remainingBalance` | decimal(15,6) | 剩余余额 |
| `equipmentStatus` | tinyint(1) | 设备状态 |
| `created_at` | datetime | 记录创建时间 |
| `unStandard` | tinyint(1) | 非标数据标记（1=异常时间记录） |

关键索引：`idx_device_time (device_id, read_time)` — 支持按设备和时间高效查询。

### 2.2 数据采集流程

SANY_check_money 的数据采集流程如下：

1. **登录认证** (`login.py`)：通过手机号和密码登录学校系统，获取 `appUserId` 和 `roleId`
2. **设备列表查询** (`check_data.py`)：分页查询用户关联的所有设备（电表/水表）信息
3. **数据入库** (`data2sql.py`)：将设备信息写入 `device` 表，将读数记录写入 `data` 表（自动去重）
4. **周期执行** (`daemon.sh`)：通过守护进程周期性地执行上述流程，持续积累历史数据

数据采集频率由 `daemon.ini` 配置的 `check_round`（检查周期，单位：秒）决定。

---

## 3. 核心算法

### 3.1 夜间时段定义

夜间时段默认定义为 **22:00 - 次日 06:00**（可配置）。该时段内，正常情况下寝室用电量应较低（主要为待机设备、空调等常规负载），若出现显著用电增量，则视为异常。

### 3.2 异常突增检测算法

算法核心思路：对比每个寝室在夜间相邻时间点的 `total_reading` 变化，识别异常突增。

#### 算法步骤

```
输入：某寝室电表在夜间时段的读数序列 [(t1, r1), (t2, r2), ..., (tn, rn)]
      其中 ti 为时间，ri 为 total_reading

步骤1：计算相邻读数差值
  diff_i = r_{i+1} - r_i  （i = 1, 2, ..., n-1）

步骤2：计算基准负荷
  baseline = median(diff)  # 使用中位数作为基准，抗异常值干扰

步骤3：计算异常得分
  对每个 diff_i：
    if diff_i > baseline * threshold_multiplier:
      anomaly_score += (diff_i - baseline) * weight(ti)
  
  weight(ti) 为时间权重函数：
    - 深夜时段（00:00-04:00）权重更高，因该时段正常用电量最低
    - 夜间边缘（22:00-00:00, 04:00-06:00）权重适中

步骤4：归一化处理
  normalized_score = anomaly_score / days_observed  # 按观测天数归一化

输出：该寝室的异常得分 normalized_score
```

#### 关键参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `night_start` | 22:00 | 夜间开始时间 |
| `night_end` | 06:00 | 夜间结束时间 |
| `threshold_multiplier` | 2.0 | 突增阈值倍数（相对于基准负荷） |
| `deep_night_weight` | 1.5 | 深夜时段（00:00-04:00）权重 |
| `edge_night_weight` | 1.0 | 夜间边缘时段权重 |

### 3.3 排名规则

1. 对所有寝室按 `normalized_score` 降序排列
2. 得分越高，表示夜间异常用电越严重
3. 支持按楼栋、楼层等维度分组排名
4. 支持设定时间范围（如最近7天、最近30天）进行统计

---

## 4. 系统架构

### 4.1 项目结构

```
SANY_ELE_RANK/
├── doc/                    # 项目文档
│   └── PROJECT.md          # 本文档
├── server/                 # 后端服务
│   ├── main.py             # 主入口，启动 API 服务
│   ├── algorithm.py        # 核心算法实现（异常检测、评分计算）
│   ├── database.py         # 数据库连接与查询封装
│   ├── ranking.py          # 排名逻辑
│   └── config/             # 后端配置
│       ├── algorithm.ini   # 算法参数配置
│       └── server.ini      # 服务器配置（端口、数据库等）
├── web/                    # 前端界面
│   ├── index.html          # 主页面
│   ├── main.js             # 前端逻辑
│   └── styles.css          # 样式文件
├── LICENSE                 # MIT 许可证
└── README.md               # 项目简介
```

### 4.2 技术栈

| 层级 | 技术选型 | 说明 |
|------|----------|------|
| 后端语言 | Python 3.x | 与 SANY_check_money 保持一致 |
| 数据库 | MySQL (共享) | 直接读取 SANY_check_money 的数据库 |
| 数据库驱动 | PyMySQL | 轻量级 MySQL 客户端 |
| Web 框架 | http.server (内置) | 轻量 RESTful API，与 SANY_check_money 架构一致 |
| 前端 | 原生 HTML/CSS/JS | 无框架依赖，轻量部署 |
| 图表库 | Chart.js | 数据可视化（排名图表、趋势图） |

### 4.3 数据流

```
┌─────────────────────────────────────────────────────────────┐
│                     SANY_ELE_RANK 数据流                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  MySQL (data表)                                             │
│       │                                                     │
│       v                                                     │
│  ┌─────────────┐    ┌──────────────┐    ┌───────────────┐  │
│  │ 数据筛选     │───>│ 夜间时段提取  │───>│ 差值计算       │  │
│  │ (电表设备)   │    │ (22:00-06:00)│    │ (相邻读数差)   │  │
│  └─────────────┘    └──────────────┘    └───────┬───────┘  │
│                                                  │          │
│                                                  v          │
│  ┌─────────────┐    ┌──────────────┐    ┌───────────────┐  │
│  │ 排名输出     │<───│ 归一化评分    │<───│ 异常突增检测   │  │
│  │ (JSON API)  │    │ (按天归一化)  │    │ (阈值判定)    │  │
│  └──────┬──────┘    └──────────────┘    └───────────────┘  │
│         │                                                   │
│         v                                                   │
│  ┌─────────────┐                                            │
│  │ Web 前端     │                                            │
│  │ (排名展示)   │                                            │
│  └─────────────┘                                            │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. API 接口设计

### 5.1 获取排名列表

```
GET /?mode=rank&days=7&building=全部&page=1&page_size=20
```

**参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `mode` | string | 是 | 固定为 `rank` |
| `days` | int | 否 | 统计天数，默认 7 |
| `building` | string | 否 | 楼栋筛选，默认"全部" |
| `page` | int | 否 | 页码，默认 1 |
| `page_size` | int | 否 | 每页条数，默认 20 |

**响应示例：**

```json
{
  "code": 200,
  "total": 150,
  "days": 7,
  "rows": [
    {
      "rank": 1,
      "device_id": "abc123",
      "installationSite": "5栋301室",
      "equipmentName": "5栋301室电表",
      "anomaly_score": 12.85,
      "peak_time": "2026-04-15 02:30:00",
      "peak_diff": 3.5,
      "avg_night_usage": 0.8
    }
  ]
}
```

### 5.2 获取寝室详情

```
GET /?mode=detail&device_id=abc123&days=7
```

**响应示例：**

```json
{
  "code": 200,
  "device_id": "abc123",
  "installationSite": "5栋301室",
  "anomaly_score": 12.85,
  "night_records": [
    {
      "date": "2026-04-15",
      "time_series": [
        {"time": "22:00", "reading": 1500.00, "diff": 0.3},
        {"time": "23:00", "reading": 1500.30, "diff": 0.2},
        {"time": "00:00", "reading": 1500.50, "diff": 2.8},
        {"time": "01:00", "reading": 1503.30, "diff": 0.1}
      ],
      "anomaly_points": ["00:00"]
    }
  ]
}
```

### 5.3 获取统计概览

```
GET /?mode=overview&days=7
```

**响应示例：**

```json
{
  "code": 200,
  "total_devices": 150,
  "anomaly_count": 23,
  "anomaly_rate": "15.3%",
  "top_building": "5栋",
  "avg_anomaly_score": 3.42
}
```

---

## 6. 前端功能

### 6.1 主要页面

1. **排名榜页面**：展示所有寝室的异常用电排名，支持按楼栋筛选和时间范围选择
2. **详情页面**：点击某寝室后展示其夜间用电时序图，标注异常突增点
3. **概览仪表盘**：展示整体统计数据（异常寝室数、异常率、高发楼栋等）

### 6.2 图表展示

- **排名柱状图**：Top N 异常寝室横向对比
- **时序折线图**：单寝室夜间用电变化曲线，异常点高亮标注
- **热力图**：按楼栋/楼层展示异常分布

---

## 7. 部署说明

### 7.1 前置条件

- Python 3.x 环境
- MySQL 数据库（由 SANY_check_money 创建和维护）
- SANY_check_money 已部署并正常采集数据

### 7.2 依赖安装

```bash
pip install pymysql
```

### 7.3 配置

1. 复制示例配置文件：
   ```bash
   cp server/config/example_server.ini server/config/server.ini
   cp server/config/example_algorithm.ini server/config/algorithm.ini
   ```

2. 修改 `server/config/server.ini`，配置数据库连接信息（应与 SANY_check_money 的数据库配置一致）：
   ```ini
   [mysql]
   mysql_server = your_mysql_host
   mysql_port = 3306
   login_user = your_username
   login_passwd = your_password
   db_schema = sany_check_money
   
   [server]
   port = 8081
   ```

3. 按需调整 `server/config/algorithm.ini` 中的算法参数：
   ```ini
   [time]
   night_start = 22:00
   night_end = 06:00
   
   [threshold]
   multiplier = 2.0
   deep_night_weight = 1.5
   edge_night_weight = 1.0
   
   [ranking]
   default_days = 7
   max_days = 90
   ```

### 7.4 启动服务

```bash
cd server
python3 main.py
```

服务默认监听 `8081` 端口（避免与 SANY_check_money 的 `8080` 端口冲突）。

### 7.5 与 SANY_check_money 协同部署

```
                    ┌─────────────┐
                    │   Nginx     │
                    │  反向代理    │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
              v            v            v
      ┌──────────┐  ┌──────────┐  ┌──────────┐
      │ 静态前端  │  │ check    │  │ ELE_RANK │
      │ web/     │  │ money    │  │ server/  │
      │          │  │ :8080    │  │ :8081    │
      └──────────┘  └──────────┘  └──────────┘
                           │            │
                           v            v
                    ┌─────────────┐
                    │   MySQL     │
                    │ (共享数据库) │
                    └─────────────┘
```

建议使用 Nginx 作为反向代理，统一对外提供服务：
- `/api/data/` -> SANY_check_money (端口 8080)
- `/api/rank/` -> SANY_ELE_RANK (端口 8081)
- `/` -> 静态前端文件

---

## 8. 开发路线

以下为项目的功能开发规划（按优先级排列）：

### 阶段一：核心功能

- [ ] 搭建后端 API 服务框架
- [ ] 实现数据库连接和数据读取（复用 SANY_check_money 数据库）
- [ ] 实现夜间时段数据提取
- [ ] 实现异常突增检测算法
- [ ] 实现寝室异常评分和排名
- [ ] 实现排名列表 API

### 阶段二：前端展示

- [ ] 搭建前端页面框架
- [ ] 实现排名榜页面
- [ ] 实现寝室详情页面（含时序图）
- [ ] 实现概览仪表盘

### 阶段三：增强功能

- [ ] 支持按楼栋/楼层分组统计
- [ ] 支持自定义时间范围查询
- [ ] 增加异常趋势分析（周/月维度）
- [ ] 增加邮件/推送通知（复用 SANY_check_money 的邮件模块）

---

## 9. 许可证

本项目采用 MIT 许可证，仅供学习和研究使用。

Copyright (c) 2026 新毛宝贝
