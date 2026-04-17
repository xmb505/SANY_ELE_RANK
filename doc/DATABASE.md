# SANY_ELE_RANK 数据库文档

## 1. 数据库概述

SANY_ELE_RANK 与 SANY_check_money 共享同一个 MySQL 数据库实例。SANY_check_money 负责数据采集和写入，SANY_ELE_RANK 读取其数据并写入自己的分析结果表。

```
┌─────────────────────────────────────────────┐
│              MySQL 数据库                    │
│                                             │
│  SANY_check_money 拥有（本项目只读）：        │
│    ├── device        设备信息表              │
│    ├── data          电表读数表              │
│    └── email         邮件订阅表              │
│                                             │
│  SANY_ELE_RANK 拥有（本项目读写）：           │
│    └── night_usage   夜间用电分析结果表       │
│                                             │
└─────────────────────────────────────────────┘
```

---

## 2. 上游表（只读）

以下表由 SANY_check_money 创建和维护，本项目仅作查询。

### 2.1 device 表（设备信息）

```sql
CREATE TABLE `device` (
  `id`               VARCHAR(32)   NOT NULL,
  `addr`             VARCHAR(20)   DEFAULT NULL,
  `equipmentName`    VARCHAR(100)  DEFAULT NULL,
  `installationSite` VARCHAR(100)  DEFAULT NULL,
  `equipmentType`    TINYINT(1)    DEFAULT NULL,
  `ratio`            DECIMAL(10,2) DEFAULT NULL,
  `rate`             DECIMAL(10,4) DEFAULT NULL,
  `acctId`           VARCHAR(20)   DEFAULT NULL,
  `status`           TINYINT(1)    DEFAULT NULL,
  `properties`       LONGTEXT      DEFAULT NULL CHECK (JSON_VALID(`properties`)),
  `created_at`       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_acctId` (`acctId`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(32) | 设备唯一标识，主键 |
| addr | VARCHAR(20) | 设备地址编号 |
| equipmentName | VARCHAR(100) | 设备名称（如"X栋XXX室电表"） |
| installationSite | VARCHAR(100) | 安装位置（寝室号） |
| equipmentType | TINYINT(1) | 设备类型：0=电表，1=水表 |
| ratio | DECIMAL(10,2) | 倍率 |
| rate | DECIMAL(10,4) | 费率（元/kWh） |
| acctId | VARCHAR(20) | 关联账户ID |
| status | TINYINT(1) | 设备状态：1=开，0=关 |
| properties | LONGTEXT | JSON 格式扩展属性 |

**本项目使用方式**：查询 `equipmentType=0`（电表）的设备，用 `id` 关联 data 表获取读数，用 `equipmentName`/`installationSite` 展示寝室信息。

### 2.2 data 表（电表读数）

```sql
CREATE TABLE `data` (
  `id`               BIGINT(20)     NOT NULL AUTO_INCREMENT,
  `device_id`        VARCHAR(32)    NOT NULL,
  `read_time`        DATETIME       NOT NULL,
  `total_reading`    DECIMAL(15,2)  DEFAULT NULL,
  `diff_reading`     DECIMAL(15,2)  DEFAULT NULL,
  `remainingBalance` DECIMAL(15,6)  DEFAULT NULL,
  `equipmentStatus`  TINYINT(1)     DEFAULT NULL,
  `created_at`       DATETIME       NOT NULL,
  `remark`           VARCHAR(255)   DEFAULT NULL,
  `unStandard`       TINYINT(1)     DEFAULT 0,
  PRIMARY KEY (`id`),
  KEY `idx_device_time` (`device_id`, `read_time`),
  CONSTRAINT `data_ibfk_1` FOREIGN KEY (`device_id`) REFERENCES `device` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT | 自增主键 |
| device_id | VARCHAR(32) | 关联设备ID（外键 -> device.id） |
| read_time | DATETIME | 电表读数时间（非严格整点，见下方说明） |
| total_reading | DECIMAL(15,2) | 累计总读数（kWh） |
| diff_reading | DECIMAL(15,2) | 差值读数 |
| remainingBalance | DECIMAL(15,6) | 剩余余额（元） |
| equipmentStatus | TINYINT(1) | 设备状态：1=开，0=关 |
| created_at | DATETIME | SANY_check_money 入库时间 |
| unStandard | TINYINT(1) | 非标数据标记：1=read_time 异常 |

**关键索引**：`idx_device_time(device_id, read_time)` 支持按设备+时间范围高效查询。

**数据特性（重要）**：
- `read_time` 不在精确整点，偏移量从几分钟到 20 多分钟
- 同一 `total_reading` 可能在相邻两次采集中重复出现
- 部分整点时刻可能完全没有数据
- 详见 `doc/ALGORITHM.md` 第 8 节

---

## 3. 本项目表（读写）

### 3.1 night_usage 表（夜间用电分析结果）

```sql
CREATE TABLE `night_usage` (
  `id`                BIGINT         NOT NULL AUTO_INCREMENT,
  `device_id`         VARCHAR(32)    NOT NULL,
  `record_time_stamp` DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `night_date`        DATE           NOT NULL,
  `n1_use_ele`        DECIMAL(10,4)  DEFAULT NULL,
  `n2_use_ele`        DECIMAL(10,4)  DEFAULT NULL,
  `n3_use_ele`        DECIMAL(10,4)  DEFAULT NULL,
  `n4_use_ele`        DECIMAL(10,4)  DEFAULT NULL,
  `n5_use_ele`        DECIMAL(10,4)  DEFAULT NULL,
  `n6_use_ele`        DECIMAL(10,4)  DEFAULT NULL,
  `n7_use_ele`        DECIMAL(10,4)  DEFAULT NULL,
  `stable_data`       TINYINT(1)     DEFAULT 0,
  `ele_score`         DECIMAL(5,2)   DEFAULT NULL,
  `score_rank`        INT            DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_device_night` (`device_id`, `night_date`),
  KEY `idx_night_date` (`night_date`),
  KEY `idx_ele_score` (`ele_score`),
  CONSTRAINT `fk_night_device` FOREIGN KEY (`device_id`) REFERENCES `device` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

#### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT | 自增主键 |
| device_id | VARCHAR(32) | 关联设备ID（外键 -> device.id） |
| record_time_stamp | DATETIME | 本条记录的写入时间 |
| night_date | DATE | 夜晚归属日期（按凌晨日期算，见下方约定） |
| n1_use_ele | DECIMAL(10,4) | 23:00-00:00 用电量（kWh） |
| n2_use_ele | DECIMAL(10,4) | 00:00-01:00 用电量（kWh） |
| n3_use_ele | DECIMAL(10,4) | 01:00-02:00 用电量（kWh） |
| n4_use_ele | DECIMAL(10,4) | 02:00-03:00 用电量（kWh） |
| n5_use_ele | DECIMAL(10,4) | 03:00-04:00 用电量（kWh） |
| n6_use_ele | DECIMAL(10,4) | 04:00-05:00 用电量（kWh） |
| n7_use_ele | DECIMAL(10,4) | 05:00-06:00 用电量（kWh） |
| stable_data | TINYINT(1) | 数据质量：1=每小时均有直接数据，0=含推断/插值 |
| ele_score | DECIMAL(5,2) | 异常能源使用得分（0~100），由算法计算 |
| score_rank | INT | 当夜排名（与 night_date 强关联，1=最高分） |

#### night_date 约定

`night_date` 表示某一晚的归属日期，**以凌晨侧的日期为准**：

```
4月15日 23:00 ~ 4月16日 06:00 这一晚 -> night_date = 2026-04-16
```

#### 索引说明

| 索引 | 类型 | 用途 |
|------|------|------|
| uk_device_night | UNIQUE | 保证同一设备同一晚只有一条记录 |
| idx_night_date | INDEX | 按日期查询某一晚所有设备的数据 |
| idx_ele_score | INDEX | 按得分排序查询 |

#### score_rank 写入时序

`score_rank` 必须在同一 `night_date` 的所有设备记录都完成 `ele_score` 计算后，统一排序回填：

```
步骤1：遍历所有电表设备，计算 n1~n7 和 ele_score，INSERT/UPDATE 记录
步骤2：按 night_date 分组，对 ele_score 降序排名，UPDATE score_rank
```

如果后续有设备补录数据，该晚的 `score_rank` 需要重算。

---

## 4. 数据流

```
data 表（原始读数）
    │
    │  查询 23:00~06:00 区间的 read_time + total_reading
    │
    v
数据预处理
    │  - 时间对齐（非整点 -> 最近整点）
    │  - 重复读数去重
    │  - 相邻读数差值 = 每小时用电量
    │  - 缺失时段插值或标记
    │
    v
night_usage 表
    │  - 写入 n1~n7, stable_data
    │  - 计算 ele_score（spike 算法）
    │  - 全设备完成后排序写入 score_rank
    │
    v
API / 前端展示
```

---

## 5. 常用查询示例

### 查询某一晚的排名（前 20）

```sql
SELECT nu.score_rank, nu.device_id, d.equipmentName, d.installationSite,
       nu.ele_score, nu.stable_data,
       nu.n1_use_ele, nu.n2_use_ele, nu.n3_use_ele, nu.n4_use_ele,
       nu.n5_use_ele, nu.n6_use_ele, nu.n7_use_ele
FROM night_usage nu
JOIN device d ON nu.device_id = d.id
WHERE nu.night_date = '2026-04-16'
ORDER BY nu.score_rank ASC
LIMIT 20;
```

### 查询某设备最近 7 晚的数据

```sql
SELECT night_date, n1_use_ele, n2_use_ele, n3_use_ele, n4_use_ele,
       n5_use_ele, n6_use_ele, n7_use_ele, ele_score, score_rank, stable_data
FROM night_usage
WHERE device_id = '25402'
ORDER BY night_date DESC
LIMIT 7;
```

### 获取某一晚原始读数（用于数据预处理）

```sql
SELECT d.device_id, d.read_time, d.total_reading
FROM data d
JOIN device dev ON d.device_id = dev.id
WHERE dev.equipmentType = 0
  AND d.read_time BETWEEN '2026-04-15 22:00:00' AND '2026-04-16 06:30:00'
  AND d.unStandard = 0
ORDER BY d.device_id, d.read_time;
```
