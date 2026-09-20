# first-sumo-simulation

用 Python 连上 SUMO，自己搭一个十字路口，然后实时控制它的红绿灯。

This is my first SUMO project: a four-arm intersection built from plain XML,
driven entirely from Python through TraCI — network generation, runtime
vehicle insertion, and a signal controller written in Python.

---

## 这东西是干嘛的

SUMO 是个交通仿真软件。它自己会按固定配时放红绿灯，但我想知道：**能不能用 Python 实时改红绿灯？**

流程是这样的：

```
我手写三个 XML（节点、道路、连接）
        ↓  netconvert 编译
   十字路口路网
        ↓  traci.start() 启动 SUMO 并连上
   Python 一边看排队情况，一边改红绿灯
```

三个脚本，一步比一步深：

- **`1_connect.py`** — 先连上，能读到数据就行
- **`2_add_vehicles.py`** — 仿真跑着的时候往里塞车
- **`3_signal_control.py`** — 用 Python 代替固定配时，管红绿灯

---

## 怎么跑

先装 [SUMO](https://sumo.dlr.de/docs/Installing.html)，然后设个环境变量：

```powershell
setx SUMO_HOME "C:\Program Files (x86)\Eclipse\Sumo"
```

> 不设也行，脚本会自己去几个常见路径找，找不到会告诉你怎么办。

```bash
git clone https://github.com/kely6669/first-sumo-simulation.git
cd first-sumo-simulation

python scripts/generate_network.py             # 生成路网
python scripts/1_connect.py                    # 连上看看
python scripts/2_add_vehicles.py               # 加车跑
python scripts/3_signal_control.py --compare   # 我的控制器 vs 固定配时
```

想看界面就加 `--gui`：

```bash
python scripts/3_signal_control.py --gui
```

不用 pip 装任何东西——`traci` 是 SUMO 自带的。

---

## 路网长这样

```
                 N
                 │
                 │
    W ────────── A ────────── E
                 │      ↑ 红绿灯在这儿
                 │
                 S
```

5 个点（4 个边界 + 1 个路口）、8 条路（每个方向一条进一条出）、12 条连接（每个进口能左转/直行/右转）。

名字的规律：`X_A` 是**往路口里开**的，`A_X` 是**从路口往外开**的。

---

## 我踩的三个坑

这部分可能是整个仓库最有用的东西。

### 一、车跑到边界不会自己消失

路网边界是死胡同，车跑完路线就停在原地不动了。我第一版跑了 600 秒，加进去 400 辆，**结果 0 辆完成、111 辆卡在网上**。

得自己动手删：

```python
if traci.vehicle.getRoadID(v).startswith("A_") and \
   traci.vehicle.getLanePosition(v) > 285:
    traci.vehicle.remove(v)
```

### 二、车流量不能超过路口的通行能力

这个坑我算了半天才想明白。

信号周期 90 秒，每个方向绿灯 24 秒，所以一个进口一小时能过：

```
1800 辆 × (24 ÷ 90) ≈ 480 辆
```

也就是**大概 7.5 秒一辆**。

我第一版设成 6 秒一辆（每个方向 600 辆/小时，四个方向加起来 2400），**远超路口总共能承受的 1920**。结果东进口积压 54 辆，最长静止 41 秒。

改成 12 秒一辆之后，网上稳定在 20 多辆，排队基本为 0。

**记住这个判断方法**：以后跑仿真看到排队一直涨，先算需求量和通行能力，别急着改代码。

### 三、我的智能控制器反而不如固定配时 😅

跑 `3_signal_control.py --compare` 的结果：

| | 固定配时 | 我的感应控制 |
|---|---|---|
| 平均排队 | **5.7** | 7.8 |
| 最大排队 | **13** | 20 |
| 通过车辆 | **72** | 68 |
| 切换次数 | 0 | 16 |

**我的控制器差了 36%。**

而且连跑 5 次结果一模一样——因为这套设置是确定性的（发车计划固定，控制器也没有随机成分），所以这不是随机波动，是真的差。

**为什么会这样？** 我的"间隙中断"策略太激进了。最小绿只有 10 秒、黄灯 3 秒，频繁切换导致每个周期浪费的时间变多。固定配时那个 24 秒的长绿反而效率更高。

**我把这个负结果留着了**，因为我觉得它比"我的算法提升了 30%"更有意思——**控制器不是越智能越好，得在具体场景下试**。

想改的话可以试：把最小绿从 10 提到 20、切换前先确认对面真有车、或者上强化学习（看 [sumo-rl](https://github.com/LucasAlegre/sumo-rl)）。

---

## 想自己改着玩

参数都在 `scripts/sumo_config.py` 顶上：

```python
LANES = 2            # 每个进口几车道
ARM_LENGTH = 300     # 路口到边界多远（米）
SPEED = 13.9         # 限速 m/s
```

控制器参数在 `scripts/3_signal_control.py`：

```python
MIN_GREEN = 10       # 最小绿灯
MAX_GREEN = 45       # 最大绿灯
```

改完直接重跑，路网会自动重新生成。几个可以试的实验：

1. 最小绿改成 20，看我的控制器能不能赢过固定配时
2. 车道数改成 1，看通行能力掉多少
3. 发车间隔全部减半，看什么时候开始堵

---

## 还没做的

- [ ] 控制器只看排队长度，没考虑等待时间和延误
- [ ] 只跑单次，没有多种子重复 + 置信区间
- [ ] 单路口，没做相邻路口的协调（绿波带）
- [ ] 车流是合成的，没做过标定

---

## 参考

- [SUMO 文档](https://sumo.dlr.de/docs/)
- [TraCI 接口](https://sumo.dlr.de/docs/TraCI/index.html)
- [sumo-rl](https://github.com/LucasAlegre/sumo-rl) — 想上强化学习的话看这个

MIT License
