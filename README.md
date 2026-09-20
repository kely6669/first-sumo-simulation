# first-sumo-simulation

用 Python + TraCI 连接 SUMO，做一个十字路口的信号控制实验。

A four-arm signalised intersection built from scratch, driven entirely from
Python through SUMO's TraCI interface: network generation, runtime vehicle
insertion, and a traffic-signal controller written in plain Python.

---

## 这个项目做了什么

```
手写 XML（节点/边/连接）
        │  netconvert 编译
        ▼
   十字路口路网  ──────────┐
                          │  traci.start()  启动 SUMO 并建立双向通道
   车流文件（车型+路线）────┤
                          ▼
              Python 实时控制信号灯
                          │
                          ▼
                   排队 / 完成量统计
```

三个阶段，对应 `scripts/` 里的三个脚本：

| 脚本 | 做什么 | 核心 API |
|---|---|---|
| `1_connect.py` | 连接 SUMO，逐步推进仿真，读实时数据 | `traci.start` / `simulationStep` / `lane.getLastStepHaltingNumber` |
| `2_add_vehicles.py` | 仿真运行中动态加车，到边界后清理 | `traci.vehicle.add` / `remove` / `getRoadID` |
| `3_signal_control.py` | 用 Python 替换固定配时，做感应式控制，并与基线对比 | `traci.trafficlight.getPhase` / `setPhase` |

---

## 快速开始

### 1. 装 SUMO

下载：https://sumo.dlr.de/docs/Installing.html

装完设置环境变量（TraCI 需要它来定位 SUMO）：

```powershell
# Windows
setx SUMO_HOME "C:\Program Files (x86)\Eclipse\Sumo"
```
```bash
# Linux / macOS
export SUMO_HOME=/usr/share/sumo
```

> 脚本会自动去 `SUMO_HOME` 找 SUMO；找不到时会依次尝试几个常见的安装路径，并在失败时给出提示。

### 2. 跑起来

```bash
git clone https://github.com/<你的用户名>/first-sumo-simulation.git
cd first-sumo-simulation

python scripts/generate_network.py          # 生成路网（调用 netconvert）
python scripts/1_connect.py                 # 连接，跑 10 秒
python scripts/2_add_vehicles.py            # 动态加车，跑 600 秒
python scripts/3_signal_control.py --compare  # 控制器 vs 固定配时
```

加 `--gui` 可以看到 SUMO 界面：

```bash
python scripts/3_signal_control.py --gui
```

依赖只有 SUMO 自带的 `traci`（随 SUMO 安装，无需 pip 安装）。

---

## 路网结构

```
                 N (300, 0)
                 │
                 │  北进口 2 车道 / 北出口 1 车道
                 │
    W (0,300) ───A (300,300)─── E (600,300)
                 │   ↑ 信号灯路口
                 │
                 S (300, 600)
```

- **5 个节点**：4 个路网边界 + 1 个中心路口
- **8 条边**：每个方向 1 条进口道（2 车道）+ 1 条出口道（1 车道）
- **12 条连接**：每个进口 → 左转 / 直行 / 右转
- **1 个信号灯**：8 个相位，周期 90 秒

边命名规则：`X_A` 是**进入**路口的车流，`A_X` 是**驶出**路口的车流。

---

## 踩过的坑（都写在代码注释里）

### 1. 车走到路网边界不会自动消失

路网边界是 `dead_end`，车跑完路线后会停在那里等，不会自己消失。
第一版跑了 600 秒，加入 400 辆，**完成 0 辆，网上积了 111 辆**。

必须自己清理：

```python
if traci.vehicle.getRoadID(v).startswith("A_") and \
   traci.vehicle.getLanePosition(v) > 285:
    traci.vehicle.remove(v)
```

### 2. 需求量必须低于通行能力，否则排队无限增长

信号周期 90 秒，每个方向绿灯约 24 秒，所以单个进口的通行能力是：

```
1800 veh/h × (24 / 90) ≈ 480 veh/h ≈ 每 7.5 秒 1 辆
```

第一版把发车间隔设成 6 秒（= 600 veh/h 每方向，合计 2400 veh/h），
远超四个方向合计约 1920 veh/h 的能力，结果东进口积压 54 辆、最长静止 41 秒。

诊断输出（`scripts/` 里可以复现）：

```
rightE_A   54 辆   最长静止 41 秒
leftW_A    25 辆   最长静止 52 秒
```

改成 12 秒间隔后，网上车辆稳定在 20~27 辆，排队 0~4 辆。

### 3. 感应控制不一定比固定配时好（本项目实测结果）

`3_signal_control.py --compare` 的实测输出：

| 指标 | 固定配时 | Python 感应控制 |
|---|---|---|
| 平均排队 | **5.7** | 7.8 |
| 最大排队 | **13** | 20 |
| 完成车辆 | **72** | 68 |
| 信号切换次数 | 0 | 16 |

**感应控制反而差了 36%。** 而且连跑 5 次结果完全一致——**这套设置是确定性的**
（发车计划固定、控制器不含随机成分），所以这个差异是本场景下的真实差异，
不是随机波动。

原因分析：间隙中断（gap-out）策略在本场景下过于激进——
路网的最小绿 10 秒、黄灯 3 秒，频繁切换导致每个周期的损失时间偏多；
而固定配时的 24 秒长绿反而更高效。

**这个负结果保留在仓库里，因为它说明的问题比正结果更有价值：**
控制器不是"越智能越好"，必须在具体需求水平下验证。

改进方向：
- 提高最小绿（10 → 20 秒），减少切换次数
- gap-out 加一个额外条件："下一相位确实有车"才切换
- 或者用强化学习让策略自己学出切换时机（见 `sumo-rl`）

**注意**：确定性只在本场景成立。换需求水平、加随机发车、或者换成学习型
控制器之后，就必须多种子重复 + 置信区间了。

---

## 可以改什么

`scripts/sumo_config.py` 顶部的参数：

```python
LANES = 2            # 每个进口的车道数
ARM_LENGTH = 300     # 路口到边界的距离（米）
SPEED = 13.9         # 限速 m/s
ROUTES = [...]       # 各方向的发车间隔
```

`scripts/3_signal_control.py`：

```python
MIN_GREEN = 10       # 最小绿灯
MAX_GREEN = 45       # 最大绿灯
```

改完直接重跑，路网会自动重新生成。

**试试这几个实验：**

1. 把 `MIN_GREEN` 从 10 改成 20，看感应控制能不能超过固定配时
2. 把 `LANES` 从 2 改成 1，看通行能力怎么变
3. 把发车间隔全部减半，看什么时候开始堵

---

## 可以改进的地方

- [ ] 控制器只用了排队长度，没有用等待时间或延误
- [ ] 单次运行没有统计意义，需要多种子重复 + 置信区间
- [ ] 单路口，没有考虑相邻路口的协调（绿波带）
- [ ] 未做标定：车流是合成的，没有真实调查数据

---

## 参考资料

- [SUMO 官方文档](https://sumo.dlr.de/docs/)
- [TraCI 接口文档](https://sumo.dlr.de/docs/TraCI/index.html)
- [netconvert 路网编译](https://sumo.dlr.de/docs/netconvert.html)
- [sumo-rl](https://github.com/LucasAlegre/sumo-rl) — 如果要把控制器换成强化学习

## License

MIT
