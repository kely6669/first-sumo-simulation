# first-sumo-simulation

> 用 Python 通过 TraCI 控制 SUMO 的红绿灯，跑完的数据用 pandas 清洗出图。

![十字路口仿真运行中：四个方向的车辆在进口道排队，红绿灯由 Python 控制，西进口有一辆公交车正在通过](docs/screenshot.png)

一条流水线，三站：**生成路网 → Python 控制仿真 → 清洗出图**。

自带一个手写的十字路口，clone 下来就能跑。**换成你自己的地方也是一条命令**——
喂它一个 OpenStreetMap 的 `.osm` 文件，它编译路网、生成车流、跑仿真、出图表。

信号控制器**不认识任何一条具体车道**：启动时它自己去问 SUMO，这个路口有几个信号灯、
每个几套相位、哪套相位给哪些车道放绿灯。所以它能用在任何有信号灯的路网上。

如果你是第一次接触 SUMO / TraCI，`1_connect.py` → `2_add_vehicles.py` →
`3_signal_control.py` 三个脚本由浅入深，每个都能独立跑通。踩过的坑都记在下面。

---

## 🌍 换成你自己的地方

这是这个仓库最实用的部分。三步：

```bash
# 1) 用你自己的地图建路网（--trips 顺便生成车流）
python scripts/build_from_osm.py --osm 你的地图.osm --trips

# 2) 跑仿真 + 出图（这一条命令全都干了）
python scripts/analyse_run.py --run-dir results/mine --simulate \
       --net-file net/你的地图.net.xml --route-file net/你的地图.rou.xml --end 3600

# 3) 想看画面就打开 SUMO 界面
sumo-gui -n net/你的地图.net.xml -r net/你的地图.rou.xml
```

**你的 `.osm` 文件从哪来？** 三种都行，脚本不关心：

| 来源 | 怎么做 |
|---|---|
| 网页导出 | [openstreetmap.org/export](https://www.openstreetmap.org/export) 框选区域 → 导出 |
| 命令行下载 | `python scripts/build_from_osm.py --bbox 116.47,39.87,116.49,39.88 --trips` |
| 现成数据包 | [Geofabrik](https://download.geofabrik.de/) 下载某个省/国家，再裁剪 |

> ⚠️ **在中国大陆，前两种方式基本都会失败。** 不是脚本的问题，是网络：
> `www.openstreetmap.org` 会被 DNS 污染（实测解析到 `157.240.0.35`，那是 Facebook 的 IP），
> 几个 Overpass 镜像要么连不上、要么**返回 200 加一份格式合法的空文档**——
> 这种"成功但为空"最坑，所以脚本会数一下文件里到底有没有路，没有就明说失败了。
>
> 用 VPN，或者让有网络的人帮你导出一份 `.osm` 发过来。**这条路 `--osm` 是完全离线的**，
> 只要文件在本地就能跑。

### 你的路网上也能控信号

`build_from_osm.py` 编译出来的路网，用 `--tls.guess` 让 netconvert 自己判断哪里该装红绿灯。
装好之后，感应控制器可以直接用：

```
python scripts/build_from_osm.py --osm 你的地图.osm --trips   # 编译（含信号灯）
# 然后在 Python 里：
from runner import run_simulation
run_simulation("results/mine", strategy="actuated",
               net_file="net/你的地图.net.xml",
               route_file="net/你的地图.rou.xml",
               drive_demand=False)      # 车流来自 .rou.xml，不由 Python 插
```

实测量级：一个 **202 个信号灯** 的真实城市路网，控制器 1.2 秒读完全部相位方案，
每仿真秒管 200 个路口。如果路网**一个信号灯都没有**，它会明确拒绝并告诉你原因，
而不是假装在工作。

---

## 🚦 这个项目能帮你做什么

SUMO 是一个开源的交通仿真软件，它自带的固定配时红绿灯很"听话"，但也仅此而已。
我好奇的是：**能不能用 Python 实时接管红绿灯？**

答案是可以，而且流程意外地简单：

```
手写三个 XML（节点、道路、连接）
        ↓  netconvert 编译
   一个十字路口路网
        ↓  traci.start() 启动 SUMO 并连接
   Python 一边看排队情况，一边改红绿灯
```

仓库里有三个脚本，难度一步比一步深，建议按顺序跑：

| 脚本 | 你会学到什么 |
|---|---|
| **`1_connect.py`** | 怎么连上 SUMO，能读到数据就算成功 |
| **`2_add_vehicles.py`** | 仿真跑着的时候往里塞车 |
| **`3_signal_control.py`** | 用 Python 代替固定配时，接管红绿灯 |

---

## 🔧 一条流水线，三站

整个仓库就是一条线，三站之间**只通过文件连接**：

```
① 生成路网                  ② Python 通过 TraCI 控制 SUMO        ③ 清洗出图
   generate_network.py         runner.run_simulation()             analyse_run.py
   build_from_osm.py           ├─ control.py   决定红绿灯          analyse_results.py
                               └─ demand.py    决定发车 / 删车
        ↓                              ↓                                ↓
   net/cross.net.xml            <run>/tripinfo.xml               metrics.csv
   net/real.net.xml             <run>/summary.xml                by_lane_queue.csv
                                <run>/queues.xml                 plot_*.png
```

第 ② 站写出来的 XML，和你手动 `sumo-gui` 跑出来的**一模一样**。
所以第 ③ 站不关心是谁开的车——人点的"播放"和 Python 驱动的仿真，它一视同仁。

### 第 ① 站 · 生成路网

| 脚本 | 产出 | 是什么 |
|---|---|---|
| `generate_network.py` | `net/cross.net.xml` | 手写 XML 的十字路口：8 条路 / 12 个连接 / 1 个信号灯 |
| `build_from_osm.py` | `net/real.net.xml` | 真实地图（OpenStreetMap），加 `--trips` 还能生成车流 |

> 📌 **编译出来的 `.net.xml` 不进仓库。**
>
> `net/cross.net.xml` 是**编译产物**：它由手写的 `net/cross.{nod,edg,con}.xml`
> 编译而来；`net/real.net.xml` 同理，来自 OpenStreetMap。
> 两个都能随时重建，而且 netconvert 每次编译都会往里写一个新的生成时间戳，
> 放进 git 只会制造无意义的 diff。
>
> **所以仓库里存的是"输入"，不是"输出"。** clone 下来只有那三个手写 XML。
>
> 不用担心少文件——**任何需要路网的脚本都会先自己检查，缺了就现编译**：
>
> ```
> $ python scripts/1_connect.py
> cross.net.xml is missing - compiling it from the hand-written XML
>   $ python .../scripts/generate_network.py
> ```
>
> 它是**明说**自己在干什么，不是偷偷补一个文件。


### 第 ② 站 · Python 控制 SUMO

| 脚本 | 干什么 |
|---|---|
| `1_connect.py` | 只连不干活，证明通道是通的 |
| `2_add_vehicles.py` | 运行时加车 + 把跑完的车删掉 |
| `3_signal_control.py` | 接管红绿灯，`--compare` 三个策略同台对比（含对照组）|
| `run_experiments.py` | 批量跑：策略 × 需求档位 × 随机种子 |

三个共享模块，每样东西**只写一份**：

| 模块 | 管什么 |
|---|---|
| `control.py` | **决定**红绿灯。四个策略 `fixed` / `timed` / `actuated` / `pressure` 都在这儿，加新策略只要写一个 `decide()` |
| `demand.py` | **决定**车流。按时刻表加车、把越过边界的车删掉 |
| `runner.py` | 把上面两件事和 SUMO 串起来，跑一次仿真。所有仿真都走这个入口 |

### 第 ③ 站 · 清洗出图

| 脚本 | 吃谁的数据 | 产出 |
|---|---|---|
| `analyse_run.py` | 一次仿真 | 指标表 + 4 个 CSV + 2 张图 |
| `analyse_results.py` | 一批仿真 | 策略对比表 + 对比图 |

两个脚本**共用同一套读取器**——`load_run` / `trip_metrics` / `network_metrics` /
`queue_metrics` 都定义在 `analyse_run.py` 里，`analyse_results.py` 直接 import。
所以"平均等待时间"只有一处定义，不会两边各算一个数。

---

## 🗺️ 两种路网：自己画的 vs 真实地图

上面那个十字路口是**手写 XML 画出来的**——结构简单、方便理解原理，但它是虚构的。

如果你想分析**某个真实地点**，用这个：

```bash
# 方式一：给一个经纬度范围，自动下载地图
python scripts/build_from_osm.py --bbox 116.470,39.875,116.485,39.888 --trips

# 方式二：用你自己从 openstreetmap.org 导出的 .osm 文件
python scripts/build_from_osm.py --osm map.osm --trips
```

它会做完三件事：

```
下载 / 读取 .osm 文件
        ↓  netconvert --osm-files
真实路网（车道数、路口形状都来自实际地图）
        ↓  randomTrips.py
车流文件（随机生成，可跑）
```

**实测效果**（用 SUMO 自带的德国某区域地图）：

| | 手写十字路口 | 真实地图 |
|---|---|---|
| 道路数 | 8 | **6,961** |
| 路口数 | 5 | **3,457** |
| 信号灯 | 1 | **199** |

建好之后直接跑：

```bash
sumo-gui -n net/real.net.xml -r net/real.net.rou.xml
```

> ⚠️ **踩过的坑**：`netconvert` 的 `--tls.guess.threshold` 参数和 `--tls.guess` 一起用会生成**损坏的路网**（信号灯定义重复，SUMO 拒绝加载）。所以脚本只用 `--tls.guess`。
>
> 而且 `netconvert` **会成功退出但写出坏文件**。所以脚本里加了"冒烟测试"——生成完立刻让 SUMO 加载一次，加载不了就报错。**别只看 netconvert 返回 0 就以为成功了。**

> 📌 OSM 数据是 ODbL 协议，写报告时要注明 **© OpenStreetMap contributors**。

---

## 🚀 五分钟跑起来

### 第一步：装 SUMO

去 [SUMO 官网](https://sumo.dlr.de/docs/Installing.html)下载安装，然后设一个环境变量：

```powershell
setx SUMO_HOME "C:\Program Files (x86)\Eclipse\Sumo"
```

> 💡 忘了设也没关系——脚本会自己去几个常见路径找 SUMO，
> 找不到时会打印提示告诉你怎么办。

### 第二步：克隆并运行

```bash
git clone https://github.com/kely6669/first-sumo-simulation.git
cd first-sumo-simulation

python scripts/generate_network.py             # 生成路网
python scripts/1_connect.py                    # 连上看看
python scripts/2_add_vehicles.py               # 加车跑起来
python scripts/3_signal_control.py --compare   # 原样 vs 对照组 vs 我的控制器
```

### 第三步（可选）：看可视化界面

加个 `--gui` 就能看到路口跑起来的样子：

```bash
python scripts/3_signal_control.py --gui
```

**依赖说明：不需要 pip 安装任何东西**——`traci` 是 SUMO 自带的，装好 SUMO 就有。

---

## 🗺️ 路网长什么样

```
                 N
                 │
                 │
    W ────────── A ────────── E
                 │      ↑ 红绿灯在这儿
                 │
                 S
```

数字很小，方便理解：**5 个点**（4 个边界 + 1 个路口）、**8 条路**（每个方向一进一出）、**12 条连接**（每个进口都能左转/直行/右转）。

命名规律看一眼就懂：`X_A` 是**往路口里开**的，`A_X` 是**从路口往外开**的。

---

## 📊 跑完的数据怎么分析

SUMO 本身只会往内存里写，**你不明确要求，关掉窗口什么都没有**。所以要输出的东西得"点名"：

```bash
# 一条命令搞定：跑仿真 + 存数据 + 出分析报告
python scripts/analyse_run.py --run-dir results/demo --simulate --end 3600
```

它做的三件事：

```
① 跑仿真，同时让 SUMO 输出四个文件
      tripinfo.xml    每辆车一条：行程时间、等待、延误
      summary.xml     每秒一条：在网车辆、排队、到达
      queues.xml      每秒每条车道的排队长度
      edgeData.xml    每条路的时段汇总

② 用 pandas 把它们变成表
      metrics.csv              一行核心指标
      by_vtype.csv             按车型分组
      by_lane_queue.csv        按车道分组（找出最堵的进口）
      summary_timeseries.csv   时间序列

③ 画图
      plot_network.png   在网车辆 + 排队随时间变化
      plot_queue.png     最堵的 5 条车道的排队曲线
```

**实测输出**（3600 秒，1320 辆车）：

```
trips_completed   1297        完成率 98%
mean_waiting_s    18.5        平均等待
median_waiting_s  10.0        中位数（比均值小一半）
p95_waiting_s     60.0        95% 的车等待不超过这个数
max_waiting_s     63.0
teleports            0   ← 必须为 0，否则数据失真
collisions           0   ← 必须为 0
```

**看这张图就懂什么叫"健康的路口"**：`plot_queue.png` 里排队呈**锯齿状周期波动**——红灯时累积、绿灯时消散，**而且不逐周期累积**。如果锯齿的谷底越来越高，说明需求超过通行能力了。

### 一批仿真怎么分析

单次跑出来的数字只是**一次抽签**。要下结论，得跑一批：

```bash
python scripts/run_experiments.py --runs 3 --duration 900 --demands 0.8 1.0 1.2
python scripts/analyse_results.py --save
```

第一条命令跑 4 策略 × 3 档需求 × 3 个随机种子 = **36 次仿真**，
每次一个目录，装的是和手动跑完全一样的 XML。第二条命令把它们汇总。
（下面坑三第六幕那张四个策略的对比表，就是这两条命令跑出来的。）

`results/runs/` 是**原子**的：每次批量实验会先清空它。
一个换了一半的实验批次，比没有批次更糟——因为里面每个数字看起来都一样可信。

汇总里最有价值的是最后那个 **spread check**：

```
headway scale 1.00
  actuated  vs fixed      diff=  4.1  spread= 0.3  -> difference > spread, likely real
  actuated  vs pressure   diff=  0.0  spread= 0.1  -> difference <= spread, NOT conclusive
  actuated  vs timed      diff=  0.0  spread= 0.2  -> difference <= spread, NOT conclusive
  fixed     vs timed      diff=  4.1  spread= 0.3  -> difference > spread, likely real
  ...
```

它比的是**"两个策略的差"**和**"随机种子造成的波动"**，
而且**每一对都比**——不是只挑两三个比。
只有差大于波动时才敢说结论成立；只要有一行波动比差大，
脚本就会老老实实写"不成立"，而不是硬报一个"我的控制器差了 2.5%"。
（上面 `actuated` vs `pressure` 那行就是它抓出来的：两个控制器在这个路口**完全等价**，
见坑三第六幕。）

> 📌 **两个种子的实验不叫结论，叫抽样。** 这个检查就是防止你自己骗自己。
>
> 但也要知道它的局限：**它只能发现"样本不够"，发现不了"代码根本没生效"。**
> 坑三里那个假结论，spread check 一路放行，因为它确实可复现——
> 见下文，那是这个仓库里最值钱的一个教训。

它还会出 `results/plot_strategies.png`：横轴是需求强度，纵轴是平均等待和平均排队，
误差棒就是种子波动。**误差棒比两条线之间的距离还长的时候，别看线，看误差棒。**

---

## 🕳️ 我踩过的三个坑

说真的，这部分可能是整个仓库最有价值的东西——都是我用报错和卡死的仿真换来的。

### 坑一：我以为车跑到边界不会自己消失——**这个判断是错的**

> ⚠️ **这一节我改过。** 原来的版本断言"SUMO 不会移除跑到边界的车，所以要手动删"，
> 并配了一段删车代码。**那个断言是错的，而且我从来没验证过它。** 下面是实测结果。

**当初的想法**：路网边界的节点是 `dead_end`（死胡同），车跑完路线应该会停在那儿不动，
网络会被"僵尸车"塞满，所以得自己动手删。

**实测是什么样**（900 秒，一行删车代码都不跑）：

```
插入 302 辆
SUMO 自己报的"到达"   279
结束时在网            23
                    ─────
              279 + 23 = 302   ← 账目完全闭合，没有一辆车卡住
```

**再把每一辆车追踪到它消失的那一步：**

| 观测 | 结果 |
|---|---|
| 从出口边消失 | **279 辆** |
| 从路口或进口边消失 | **0 辆** |
| 消失时最后记录的位置 | 275.9 ~ 289.5 米 |
| 出口边的**实际**长度 | **289.60 米**（不是 300，路口转角半径要减掉） |

一步最多走约 14 米，所以"最后在 276 米"就等于"实际在 289.60 米处消失"。
**279 辆车全部走到了路线终点，被 SUMO 自己移除了。** `dead_end` 不困车。

**所以那段删车代码不只是多余的，它还有害：**

1. 它在 **285 米**处删车，比真正的终点（289.60 米）早 **4.6 米**，约 0.33 秒
2. **`traci.vehicle.remove()` 会被 SUMO 计入 `arrived`**。开着手动删车跑一遍：

```
插入 302
SUMO 报到达  280  ← 这里面包含了我删掉的
我手动删的   116
在网          22
            ─────
      280 + 116 + 22 = 418    ← 302 辆车跑出了 418 个结果
```

**手动删掉的车被记成了"正常到达"，统计被污染了。**

**代码已经删掉了。** `demand.py` 现在只负责插车，完成的行程数直接读 SUMO 自己的
`arrived` 计数（也就是 `summary.xml` 里那个）。

> 📌 **这件事的教训不是"SUMO 怎么怎么样"，是——**
> **我在注释里写了一个"机制"，但从没测过它。**
> "因为 A 所以 B"这种话，写之前应该先把 A 验证一遍。
> 而且这段错误的理解在代码里活了好几个月，因为它**看起来很合理**。

### 坑二：车流量不能超过路口的通行能力

这个坑我算了半天才想明白，分享给你省点时间。

信号周期 90 秒，每个方向绿灯 24 秒，所以一个进口一小时最多能过：

```
1800 辆 × (24 ÷ 90) ≈ 480 辆
```

也就是**大约 7.5 秒一辆**，不能再快了。

我第一版设成 6 秒一辆（四个方向合计 2400 辆/小时），**远超路口能承受的 1920**。
结果东进口积压 54 辆，最长静止 41 秒——堵得明明白白。

改成 12 秒一辆之后，网上稳定在 20 多辆，排队基本为 0。

> 📌 **记住这个排查思路**：以后跑仿真看到排队一直涨，先算"需求量 vs 通行能力"，别急着改代码。

**而且"通行能力"不只看信号灯——出口车道也卡脖子。**

做 `flow.rou.xml` 的时候我又撞了一次：这次不是进口超了，是**出口超了**。
每个方向的出口道只有 1 条车道，却有**两股车流共用**它：

```
西出口 A_leftW   ← 东进口的车直行过来 240 辆/小时
                 ← 西进口的车左转过来  60 辆/小时

东出口 A_rightE  ← 西进口的车直行过来 400 辆/小时   ← 就是我第一版设的
                 ← 东进口的车右转过来  60 辆/小时
```

第一版给了西进口 400 辆/小时，全压到东出口上。结果排队**一路涨到仿真结束**：

```
每 600 秒一段的平均排队：12.3 → 21.2 → 30.5 → 38.3 → 45.7   ← 从不回落
```

把四个方向调平衡之后（每个出口 300~340 辆/小时）：

```
每 600 秒一段的平均排队：6.7 → 6.9 → 6.7 → 6.5 → 6.8        ← 完全平稳
峰值排队从 71 降到 16
```

> 📌 **所以排查"排队一直涨"要看两处**：进口道的信号能力、**出口车道的能力**。
> 单车道出口的实际通行能力大约 700~900 辆/小时（受信号影响），超过就会堵。

### 坑三：我的控制器看着在工作，其实一直在跟固定配时抢方向盘 🕵️

这个坑比"我的算法提升了 X%"有价值得多，因为它是一个**测量错误**——而且它一开始伪装成了一个漂亮的负结果。

#### 第一幕：一个看起来很诚实的负结果

我写完感应控制（gap-out），跑对比，结果是它比固定配时**差 47%**：

| 指标 | 固定配时 | 我的感应控制 | 变化 |
|---|---|---|---|
| 平均排队 | **16.18 m** | 25.37 m | +56.8% |
| 平均等待 | **18.50 s** | 27.30 s | +47.6% |
| 相位切换 | 0 | 23 | — |

> 📌 **这张表来自修复前的代码，现在复现不出来了**——因为下面讲的那个 bug 已经被修掉。
> 保留它是为了让这个故事的起点完整。

我当时还挺满意：**"看，我把负结果留下来了，我很诚实。"**
而且连跑几次数字一模一样——确定性的，不是运气。

#### 第二幕：为了支持别人的路网，我把控制器改成通用的

原来的控制器把车道名写死了（`rightE_A_0` 这些）。这在别人的路网上会**静默失效**：
读不到那些车道 → 队列永远是 0 → **控制器以为路是空的**，什么也不做，还不报错。

所以我改成启动时去问 SUMO：`getControlledLinks()` 拿到每条受控连接，
`getAllProgramLogics()` 拿到相位表，然后自己算出每个相位给哪些车道放绿灯。

改完先做探测。读出来的东西让我停住了：

```
phase 0: 24.0s  rrgGrrGGgGrr   绿灯位=[2,3,6,7,8,9]
phase 1:  3.0s  rryGrryyyGrr   绿灯位=[3,9]      <- 这是黄灯
phase 2: 24.0s  rrrGGgrrrGGg   绿灯位=[3,4,5,9,10,11]
phase 4:  6.0s  rrrrrGrrrrrG   绿灯位=[5,11]     <- 旧代码完全漏掉了这个相位
```

**然后我试了一下 `setPhase()` 到底管不管用：**

```
setPhase("A", 2)          # 跳到相位 2
t=25s 相位 2 -> 3         # SUMO 自己往下走了
t=28s 相位 3 -> 4
t=34s 相位 4 -> 5
```

**`setPhase()` 只是"跳"过去，跳完 SUMO 照样跑自己的程序。**

也就是说，我那个"感应控制器"**确实在改信号灯**（`setPhase` 是生效的），
但它是在 SUMO 已经配好的固定配时上**乱插队**——下一幕会看到，它插得比我想的还离谱。
那 47% 不是"我的算法差"，是"我在破坏一个本来就配好的方案"。

#### 第三幕：真正的问题不在 `setPhase`，在我跳错了地方

`setPhase()` 本身没毛病——它确实能立刻改相位。问题是我让它**从一个绿灯直接跳到另一个绿灯**：

```python
NEXT_GREEN = {0: 2, 2: 6, 6: 0}
traci.trafficlight.setPhase(TLS_ID, NEXT_GREEN[phase])    # 旧代码
```

相位 0 → 相位 2，中间那个**黄灯（相位 1）被整个跳过了**。
一个从绿灯直接切到冲突绿灯的路口，那不叫配时方案，那叫撞车。

正确做法是**跳到程序里的下一个相位**，也就是黄灯，然后让 SUMO 用自己的时长跑完黄灯和全红，
自己走到下一个绿灯：

```python
traci.trafficlight.setPhase(tls_id, (phase + 1) % plan.phase_count)
```

> 📌 顺带记一个当时探出来的结论：`setPhase` + `setPhaseDuration` 能把一个相位**按住**
> （实测 200 步后仍停在相位 2）。**这份代码不用它**——因为控制器只提前结束绿灯、从不延长，
> 所以没有任何相位需要按住。你要做"延长绿灯"的策略，那才需要它。

#### 第四幕：修好之后，结论翻了个个儿

| 指标 | 固定配时 | 感应控制 | 变化 |
|---|---|---|---|
| 平均排队 | 16.23 m | **13.19 m** | **−18.7%** |
| 平均等待 | 18.50 s | **14.40 s** | **−22%** |
| 相位切换 | 0 | 11 | — |

> ⚠️ **这一幕的结论后来又被推翻了一次**——不是代码错了，是**我比错了对象**。
> 见第六幕。

而且三种需求档位全赢，spread check 全部判定"差异是真的"：

```
headway scale 0.80
  actuated  vs fixed      diff=  8.6  spread= 0.9  -> difference > spread, likely real
headway scale 1.00
  actuated  vs fixed      diff=  4.1  spread= 0.3  -> difference > spread, likely real
headway scale 1.20
  actuated  vs fixed      diff=  5.8  spread= 0.3  -> difference > spread, likely real
```

> 📌 **第一版那个"负结果"是假的。** 它之所以看起来像真的，恰恰是因为**它可复现**——
> 同一套设置跑多少次都是 47%。
>
> **可复现的错误和可复现的结论，在结果表里长得一模一样。**
> 分辨它们的办法不是多跑几次，是**去看你的代码到底做了什么**。
> 我当时差点就把这个假结论写成"控制器不是越智能越好"的人生感悟了。

#### 第五幕：连 gap-out 的判定条件也是错的

真正的相位表一读出来就发现：phase 0 和 phase 2 **共享** `rightE_A_0`、`leftW_A_0`
两条车道（主干道直行在两个相位里都放绿）。

于是"当前绿灯方向全空了"这个条件几乎永远不成立——**共享车道一空，两边的计数同时归零**。
实测 600 秒里成立 **0 次**：控制器一次都不切换，安安静静什么都不做，
而结果表上它和固定配时**一模一样**，看起来"没有副作用"。

改成只统计**真正会换状态**的车道——会失去绿灯的、会获得绿灯的，共享车道两边都不算：

```python
here, there = set(lanes[phase]), set(lanes[next_green[phase]])
losing  = here - there      # 切了会失去绿灯
gaining = there - here      # 切了会获得绿灯
```

同一段 600 秒里成立 **9 次**。README 里那个"最小绿 10 秒"的配置，
现在是真的在起作用了。

#### 第六幕：我赢的那个"固定配时"，其实是 netconvert 随手写的

第四幕那 22% 不是编的，但它**证明不了我以为它证明的事**。两个原因，都很朴素：

**问题一：`fixed` 不是一个"配好的方案"。**
它是 netconvert 转换路网时顺手写进 `.net.xml` 的默认值——四相位、90 秒周期、绿灯 24/24/6/24。
没人调过它。打赢一个没人调过的默认值，只能说明默认值不好，**不能说明我的控制器好**。

**问题二：我的控制器顺手改掉了周期长度，而周期本身就会影响延误。**
90 秒周期意味着一个方向最多干等 90 秒；周期缩短，等待自然下降——
**这跟"有没有在看排队"一点关系都没有**。

两个变量混在一起，所以那个结论不成立。拆开的办法是加一个**对照组**。

于是有了第四个策略 `timed`：**同样是固定配时，但它完全不看车**，
每个绿灯只按秒数放行，到点就切。它只回答一个问题：

> 如果我只把周期改短、不装任何"智能"，能拿到多少？

扫一遍绿灯秒数（900 秒，同一个种子）：

| 绿灯时长 | 实测周期 | 平均等待 | 相位切换 |
|---|---|---|---|
| 6 s | 39.0 s | 39.10 s | 69 |
| 8 s | 45.0 s | 17.90 s | 60 |
| **10 s** | **50.9 s** | **11.20 s** | **53** |
| 12 s | 56.9 s | 12.30 s | 47 |
| 15 s | 65.9 s | 14.60 s | 41 |
| 18 s | 74.9 s | 15.30 s | 36 |
| 20 s | 80.9 s | 16.70 s | 33 |
| 22 s | 86.9 s | 17.30 s | 31 |
| 25 s | 90.0 s | 18.50 s | 0 |

最后一行已经**切不动了**：25 秒比计划里最长的绿灯（24 秒）还长，
控制器永远轮不到出手，于是它退化成 `fixed`，数字也一模一样。

四个策略放一起（900 秒 × 3 个种子，平均等待，秒）：

| 需求档位 | `fixed` | `timed`(15s) | `actuated` | `pressure` |
|---|---|---|---|---|
| 0.80（车多） | 24.1 | 19.2 | **15.5** | **15.5** |
| 1.00 | 18.5 | 14.5 | **14.5** | **14.5** |
| 1.20（车少） | 19.4 | **13.1** | 13.6 | 13.6 |

三个结论，一个比一个难看：

**① 最笨的那个赢了。**
同一个种子、同一段 900 秒，10 秒绿灯的傻瓜固定配时是 **11.20 秒**，
我的感应控制是 **14.40 秒**。不看车，反而更快。

**② 同样周期下比，优势缩水一大半。**
感应控制跑出来的周期是 **78.2 秒**。`timed` 在绿灯 18 秒时周期 74.9 秒、20 秒时 80.9 秒，
正好把它夹在中间——所以"周期一样长"的公平对比里，`timed` 大约是 **15.3 ~ 16.7 秒**，
我 14.5 秒。**真正因为"看了排队"赚到的只有 1~2 秒（10% 上下）**，不是 22%。

**③ `pressure` 和 `actuated` 一模一样，一位小数都不差。**

不是巧合，是几何决定的。把每个绿灯相位的**出口车道**打出来：

```
phase 0: out = [A_bottomN_0, A_leftW_0, A_rightE_0, A_topS_0]
phase 2: out = [A_bottomN_0, A_leftW_0, A_rightE_0, A_topS_0]
phase 6: out = [A_bottomN_0, A_leftW_0, A_rightE_0, A_topS_0]
```

phase 0/2/6 放行的车**汇进完全相同的四条出口车道**。
Max Pressure 的判据是 `排队(进口) − 排队(出口)`，三个相位的出口项完全相同、**直接抵消**，
判据退化成"比一比谁排队长"——在这个路口，它给出的答案和感应控制的布尔条件一模一样。

更根本的是：**整个 900 秒里，控制器只动过 phase 0 这一个相位。**

| 相位 | 计划绿灯 | 实际跑成 | 被控制器切过 |
|---|---|---|---|
| 0 | 24 s | 11 s | 11 次 |
| 2 | 24 s | 24 s | 0 |
| 4 | 6 s | 6 s | 0 |
| 6 | 24 s | 24 s | 0 |

- phase 2 的 `gaining` 是**空集**（切过去没有任何车道新获得绿灯），gap-out 永远不成立；
- phase 4 只有 6 秒，比 `MIN_GREEN=10` 还短，控制器根本来不及插手；
- phase 6 的条件一次都没满足过。

所以这个"感应控制"在本路口做的全部事情，其实是**把一个绿灯从 24 秒砍到 11 秒**。

> 📌 **一个赢不了的实验，比一个赢了的实验有用。**
>
> 这次不是代码错了——代码是对的，数字也是可复现的。
> 错的是**我拿它去比谁**（一个没人调过的默认方案），
> 以及**我只有一个场景**。
>
> 四个绿灯相位里只有一个能动、一个不看车的傻瓜方案就能赢，
> 说明这个路口**根本没有区分控制器的能力**。
> 拿它去排"哪个算法更好"，排出来的只是噪声。
>
> 想真的比出东西，得换有多个路口、每个方向有独立出口、需求有波动的场景——
> 比如 [RESCO](https://github.com/Pi-Star-Lab/RESCO) 那套真实路网
> （Cologne、Ingolstadt、Salt Lake City，外加 arterial4x4 / grid4x4 两个合成路网）。
> 它用的就是同一套 SUMO，每个场景只是三个文件：`.net.xml` + `.rou.xml` + `.sumocfg`。
> 它同时提供了这个领域真正的三条基准线：**Fixed Time、Max Pressure、Max Wave**。
>
> 顺带一提：MIT 2022 年那篇 [NeurIPS 论文](https://ar5iv.labs.arxiv.org/html/2210.08607)
> 就是拿 RESCO 做的案例研究——在 164 个 Salt Lake City 路口上重测，
> **不学习的 Fixed Time 和 Max Pressure 打赢了四个 DRL 方法**。
> 这个领域里，"我的方法赢了"这句话，得先问清楚它赢的是谁。

#### 第七幕：换到真实路网，一半的结论活下来了

第六幕结尾我说"得换个能区分控制器的场景"。我去换了，用的是
[RESCO](https://github.com/Pi-Star-Lab/RESCO) 的四个真实路网：

| 场景 | 信号灯 | 车流 | 信号周期 |
|---|---|---|---|
| ingolstadt1（德国）| 1 | 1716 辆/小时 | 90 s |
| cologne3（科隆）| 3 | 4494 辆/小时 | 90 s |
| ingolstadt7（德国）| 7 | 3031 辆/小时 | 90 s |
| cologne8（科隆）| 8 | 2046 辆/小时 | 90 s |

> ⚠️ **cologne1 用不了，这里记一下。** 它的信号方案是**占位符**：8 个相位全是
> 5 秒、一个黄灯都没有。控制器每秒看一次，等它看到"这个绿灯已经亮了 10 秒"，
> 那个相位早就过去了——实测 `switches = 0`，一次都没动过。
> 选场景之前先看相位表，别假设下载来的路网配时一定是真的。

每个场景跑四个策略 × 5 个随机种子，指标是"平均每辆车等多少秒"：

| 场景 | `fixed` | `timed` | `actuated` | `pressure` |
|---|---|---|---|---|
| ingolstadt1 | 20.3 ± 0.5 | **10.7 ± 0.2** | 15.8 ± 0.5 | 17.3 ± 1.5 |
| cologne8 | 30.6 ± 0.7 | **27.3 ± 1.3** | 28.7 ± 0.6 | 29.1 ± 1.5 |
| ingolstadt7 | 50.4 ± 1.7 | 36.4 ± 1.0 | 36.2 ± 2.3 | **34.1 ± 2.3** |
| cologne3 | **48.3 ± 2.5** | 51.0 ± 3.1 | 49.7 ± 1.2 | 51.1 ± 1.9 |

`±` 是 5 个种子之间的标准差。**种子是必须跑的**：同一套设置换个种子，
ingolstadt7 的 `fixed` 能从 47.4 变到 51.4，比好几个策略之间的差距还大。

**三条结论，只有第一条是完全站得住的：**

**① 路网自带的方案很差，换掉它就省一大截。**
四个场景里三个，`fixed` 是最差的，而且差得多——ingolstadt1 差一倍，
ingolstadt7 差 15 秒。这两个差距远超种子波动，是真的。

**② 但一个傻瓜固定配时就把这份好处基本吃干净了。**
`timed` 用同一个 15 秒设置跑遍全部四个场景，**没有针对任何一个调过**，
却两个场景最好、一个打平，只在过饱和的 cologne3 上更差。

**③ 三个"非默认"策略之间的差距，大多只是勉强超过噪声。**

- ingolstadt1：`timed` 领先 `actuated` 5.1 秒，两边波动 0.2 / 0.5 —— **是真的**。
- cologne8：领先 1.4 秒，可 `timed` 自己跑 5 次就抖了 3.5 秒 —— **说不清**。
- ingolstadt7：`pressure` 领先 2.3 秒，波动 2.3 / 1.0 —— **勉强**。
- cologne3：四个策略全在噪声里。

**还有一处是我自己打自己。** 第六幕里"10 秒绿灯"是十字路口的最优值，
换到 ingolstadt1 一测**也是 10 秒**，当时我以为找到规律了。
把另外三个场景也扫一遍，最优点分别是 8 秒、20 秒、15 秒。
**"10 秒"不是规律，是那两个路口的答案。**

**所以"最笨的方案赢了"到底活下来没有？活下来了，但形状变了：**

- **活的**：一个不看车流的固定配时，确实能打赢路网自带的方案，
  也确实能和自适应控制器打平甚至更好。从手写十字路口一路复现到德国和卢森堡的真实路口。
- **变了的**：单点路口上控制器根本没有操作空间（第六幕：三个绿灯相位只有一个能动）；
  到了多路口场景，自适应控制器能追上来，`pressure` 在 ingolstadt7 上还是最好的。
- **新的一条**：**过饱和时怎么控都没用。** cologne3 是 4494 辆/小时的场景，
  四个策略的差距全在噪声里——信号控制有它的作用边界。

> 📌 **这一节我做对的只有一件事：给每个数字配上了种子波动。**
>
> 没有它，我会把两件假的东西写成真的：
>
> 一是 ingolstadt1 上 `pressure` 和 `actuated` **种子 0 恰好逐秒相同**，
> 我当时已经把它当成"机制"解释了；换成 5 个种子，它们立刻分开（15.8 vs 17.3）。
> 二是 cologne8 上那 1.4 秒的差距——它比这个策略自己的抖动还小。
>
> **同一个实验，加不加误差棒，能得出完全相反的结论。**

**怎么自己跑一遍**（约 40 分钟，80 次仿真）：

```bash
# 1) 下载场景文件。RESCO 是 GPL-3.0，本仓库是 MIT，所以不随仓库分发，
#    放在 net/resco/（已 gitignore）
BASE=https://raw.githubusercontent.com/Pi-Star-Lab/RESCO/main/resco_benchmark/environments
mkdir -p net/resco
for S in ingolstadt1 ingolstadt7 cologne3 cologne8; do
  for F in $S.net.xml $S.rou.xml; do
    curl -L -o net/resco/$F $BASE/$S/$F
  done
done

# 2) 四个策略 × 5 个种子。--route-file 表示车流由文件决定（真实场景都是这样），
#    --begin 是仿真时钟的起点：这些车是 16:00 或 07:00 出发的，从 0 开始会跑一小时空路
python scripts/run_experiments.py \
    --net-file net/resco/ingolstadt1.net.xml \
    --route-file net/resco/ingolstadt1.rou.xml \
    --begin 57600 --duration 3600 --runs 5 \
    --results-dir results/resco

# 3) 汇总。每个场景单独一个 --results-dir，因为一个批次是原子的，会先清空目录
python scripts/analyse_results.py --results-dir results/resco --save
```

各场景的起点不一样（cologne 系列是 25200，ingolstadt 系列是 57600），
cologne3 的车从 23512 就开始发，所以它要 `--begin 23512 --duration 5300`。

---

## 🔧 想自己改着玩

参数都集中在 `scripts/sumo_config.py` 顶部，改起来很方便：

```python
LANES = 2            # 每个进口几车道
ARM_LENGTH = 300     # 路口到边界多远（米）
SPEED = 13.9         # 限速 m/s
ROUTES = [...]       # 每个方向的车流量（headway 秒）
```

控制器参数在 `scripts/control.py`：

```python
MIN_GREEN = 10       # 最小绿灯，绝不低于这个
MAX_GREEN = 45       # 最大绿灯，超过就强制切换
```

> 📌 **关于 `MAX_GREEN` 的一个诚实说明**：控制器**只能把绿灯提前结束，
> 不能把它延长**。所以 `MAX_GREEN` 只在"路网自己的绿灯比它还长"时才起作用。
> 十字路口的每个绿灯都是 24 秒（< 45），所以在这个路网上 max-out **永远不会触发**——
> 它已经由路网自己的配时管住了。
>
> 为什么不做"延长"？因为**延长会把横向饿死**。十字路口有个 phase 4 只有 6 秒，
> 如果控制器能把它按住 45 秒，另外三个方向就得干等。**"提前结束"不会饿死任何人，
> 所以控制器只拿这一项权力。**

**控制器不知道任何一条具体车道。** 它启动时自己去问 SUMO：

```python
links  = traci.trafficlight.getControlledLinks(tls)      # 每条受控连接
program = traci.trafficlight.getAllProgramLogics(tls)[0] # 相位表
# state[i] in "Gg"  ->  第 i 条连接是绿灯
# 含 'y' 的相位是过渡相位，不能停
```

所以同一个 `actuated` 能直接用在别人的路网上。实测量级：**202 个信号灯**的真实城市路网，
1.2 秒读完全部方案。路网一个信号灯都没有时，它会明确拒绝并说明原因。

**想加一个自己的控制器**？继承 `SignalController`，实现一个 `decide()`，加进 `CONTROLLERS` 就行：

```python
class MyController(SignalController):
    name = "mine"

    def decide(self, plan, phase, elapsed):
        """要不要现在结束这个绿灯？elapsed 是它已经跑了多少秒。"""
        return elapsed >= 20        # 或者任何你想得到的东西

CONTROLLERS["mine"] = MyController
```

读相位表、判断绿灯从哪一秒开始、切完黄灯怎么走——**基类里都写好了**，
你只需要回答"切不切"这一个问题。然后
`python scripts/run_experiments.py --strategies fixed mine` 就能和固定配时对比了。
**其它文件一行都不用改**——把 `decide()` 里换成强化学习、遗传算法或模糊控制，就是一个研究贡献。

> ⚠️ 自己写控制器时记住坑三的教训，分清楚两件事：
>
> - **提前结束**绿灯：`setPhase(tls, next_phase)` 就够了，SUMO 会自己跑完黄灯。
>   这里的控制器全都只做这件事，所以它们**不碰** `setPhaseDuration`。
> - **按住 / 延长**一个相位：那才需要 `setPhaseDuration(tls, seconds)`。
>
> 旧代码真正的错误是让 `setPhase` **从绿灯直接跳到下一个绿灯**，把黄灯整个跳过了。
> 这个坑的代价是一个假结论，见坑三。

改完直接重跑就行，路网会自动重新生成。几个值得试的小实验：

1. 最小绿从 10 改成 20，看控制器是变好还是变差（先看坑三第六幕，别急着高兴）
2. 车道数改成 1，看通行能力掉多少
3. 发车间隔全部减半，看什么时候开始堵
4. 把 `--runs` 提到 10，看 spread check 的结论会不会变
5. 拿一份**你自己城市**的 `.osm` 跑一遍，看控制器的结论还成不成立
6. 改 `control.py` 里的 `TIMED_SWITCH`，看第六幕那张表换个数是不是还成立

---

## 📝 还没做的（欢迎来补）

- [x] 多种子重复 + 波动检查（`run_experiments.py` + spread check）
- [x] 控制器和具体路网解耦，能用在别人的路网上（`control.discover_plans`）
- [x] 一个仿真入口，两种车流来源（`runner.run_simulation` 的 `drive_demand`）
- [x] 固定配时对照组 + Max Pressure，四个策略同台（`control.py`）
- [x] 换成真实路网再测一遍，并带上种子误差棒（坑三第七幕，`run_experiments.py --net-file`）
- [ ] **用带时变车流的场景再测一次**——现在的场景都是稳定车流，自适应控制的价值本来应该体现在车流变化上（RESCO 的 Salt Lake City 场景带一整年真实流量，还没接）
- [ ] 控制器只看排队长度，没考虑等待时间和延误
- [ ] `pressure` 的输出车道项在共享出口时会抵消，需要专门构造一个非退化的路网才能验它
- [ ] 只管单点，没做相邻路口的协调（绿波带）；多路口时每步要查几百次 TraCI，能再快
- [ ] 车流是合成的，没做过标定
- [ ] spread check 只是"差 vs 波动"的粗判，还没接正式的统计检验（Mann-Whitney / t 检验）；第七幕里那些"勉强"的结论，正是需要正式检验的地方

---

## 📚 参考资料

- [SUMO 官方文档](https://sumo.dlr.de/docs/)
- [TraCI 接口文档](https://sumo.dlr.de/docs/TraCI/index.html)
- [RESCO](https://github.com/Pi-Star-Lab/RESCO) — 真实路网 + 固定配时 / Max Pressure / Max Wave 基准。坑三第六幕提到它，第七幕用的就是它的四个场景（GPL-3.0，所以仓库里不附带，README 里有下载命令）
- [sumo-rl](https://github.com/LucasAlegre/sumo-rl) — 想上强化学习的话看这个

---

MIT License — 随便用，玩得开心 🎉
