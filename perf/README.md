# SPEC 性能计数器统计

在当前目录安装依赖并运行（Python 3.10+）：

```bash
python3 -m pip install -r requirements.txt
python3 main.py --data baseline:/path/to/data cycles
python3 main.py --data /path/to/data ipc
python3 main.py --data baseline:/path/to/data1 --data optimized:/path/to/data2 ipc --output figures/ipc.png
python3 main.py --data /path/to/data --json /path/to/checkpoints_cov0.3.json --jobs 8 ipc
```

`--data` 必填，可重复使用，每次接收一个 `[NAME:]PATH`。显式名称用于图例和
CSV 表头；裸路径使用目录 basename。名称必须唯一。优先识别已存在的裸目录；
否则仅当第一个冒号前是合法名称（字母、数字、下划线起始，其后可包含点、
短横线和空格）时，将其识别为 `NAME:PATH`，因此路径部分可包含冒号。

每个根目录下一级 checkpoint 目录名应为 `WORKLOAD_CHECKPOINT_WEIGHT`，例如：

```text
data/
  500.perlbench_rate_refrate_00_checkspam.2500.5.25.11.150.1.1.1.1_11075_0.044852/
    simulator_err.txt
```

从右侧解析 checkpoint ID（整数）和权重（有限非负数），workload 第一个下划线
前的部分作为 benchmark，示例对应 `500.perlbench`。所有该 benchmark 的输入和
checkpoint 直接按 `Σ(value × weight)` 求和，不除以权重总和。零权重不参与统计。
缺少日志或命名不符合格式的目录输出 warning 并跳过，隐藏目录忽略。

`--json PATH` 可指定 checkpoint profile，格式与
`/nfs/home/share/checkpoints_profiles/spec06_gcc16_rva23_novec_260820/json/checkpoints_cov0.3.json`
相同，例如：

```json
{
  "gcc_166": {
    "insts": "123456789",
    "points": {"2119": "0.25", "3001": "0.75"}
  }
}
```

指定 JSON 后，使用完整的 `WORKLOAD_CHECKPOINT_WEIGHT` 目录名精确匹配；例如
上面的 JSON 只选择 `gcc_166_2119_0.25` 和 `gcc_166_3001_0.75`。checkpoint
序号可以重复，例如 `astar_biglakes_439_0.009537` 和
`astar_biglakes_439_0.010087` 是不同 checkpoint，只加载 JSON 指定权重对应的目录。
优先使用权重字符串的原始格式（包括末尾的零）；JSON 数字使用其字符串表示。
原始名称不存在时，按 CI 目录的六位小数格式构造完整名称再精确匹配，例如
JSON 的 `0.1801` 可定位 `gcc_166_2119_0.180100`。两种名称都存在时只选原始名称。
使用 JSON 的 `points` 原始权重计算加权和，不对计算权重取六位小数。
`insts` 不参与计算，仍按
`Σ(value × JSON weight)` 汇总到 benchmark，不归一化。所有数据源使用相同的
JSON 筛选与权重。缺少精确匹配的目录时输出 warning，列出完整目录名。
不传 `--json` 则使用目录名中的权重。

日志格式以实际 XiangShan 输出为准：

```text
[PERF ][time=13791598] SimTop.cpu.l_soc.core_with_l2.core.backend.inner.ctrlBlock.rob: clock_cycle, 7226263
```

每个文件只取最大 `time` 的快照，遇到更大的时间会清除之前所有匹配结果；之后
出现的较小时间忽略。相同路径在最大时间重复出现时保留最后一个值，不同路径
的匹配项累加。没有匹配项与数值为零有区别：某基础 alias 在 benchmark 的任一
已读取、非零权重 checkpoint 的最终快照缺失，就不会使用该 alias 的部分加权和。
无法得到目标值的 benchmark 输出 warning 并跳过；数据源没有可用结果时报错。

在 [main.py](main.py) 的 `COUNTERS` 字典中配置 alias：

```python
COUNTERS = {
    "cycles": r"\brob:\s*clock_cycle$",
    "instructions": r"\brob:\s*commitInstr$",
    "stalls": [r"^IntNotReadyStall$", r"^FPNotReadyStall$"],
    "ipc": lambda values: values["instructions"] / values["cycles"],
}
```

字符串和列表使用 `re.search` 匹配完整的 `module:counter`（同时兼容日志中
`module: counter` 的空格写法）以及末端 counter 名称；建议使用 `^` / `$`
避免误匹配。列表是多个正则的并集，
同一路径即使匹配多个正则也只计一次。Callable 接收所有基础 alias **加权后**的
字典，不接收其他 Callable 的结果，因此 IPC 是加权指令数 / 加权周期数。
未定义的命令行 counter 直接作为正则，例如：

```bash
python3 main.py --data /path/to/data '^TAGEMissBubble$'
```

单源使用一种颜色，多源按 benchmark 分组并使用不同颜色，组内柱子之间留少量
间距，图例显示数据源名称。
加载时为每个数据源显示 `tqdm` 进度条，包含 checkpoint 数量、进度和加载速度。
默认最多使用 8 个进程（不超过 CPU 数量）并行解析 checkpoint，`--jobs N` / `-j N`
可调整，`--jobs 1` 使用串行模式。进度按实际完成的文件更新，聚合仍按固定目录
顺序执行。每个进程缓存计数器路径的正则匹配结果，只解析命中项的数值。
提取结果持久化到当前工作目录的 `.cache/<hash>.json`，hash 为数据目录规范化
绝对路径的 SHA-256；同一路径更换图例名称时复用缓存。只有请求的 counter
短名称（或直接输入的正则）不在缓存中时才加载日志。每次加载只扫描一遍日志，
同时匹配当前 `COUNTERS` 字典内所有基础 alias，再用加权后的基础数据计算所有
Callable alias，将每个可计算项的完整 `benchmark -> value` 分别写入缓存。
因此首次请求 `cycles` 后，切换到 `instructions`、`ipc` 等已提取的 alias 无需
再次读取日志。直接输入正则时也会同时提取所有配置的 alias。
无法匹配或计算的其他 alias 跳过，不影响当前 counter。不同 `--json` 筛选及
权重独立缓存，JSON 内容相同则可复用。调整坐标轴、图片尺寸或字体时直接使用
缓存重新绘图。
缓存不包含 `GEOMEAN`，它始终在选取多源交集后重新计算。

日志更新或修改 `COUNTERS` 定义后，使用 `--force-update` 强制重新提取当前
`COUNTERS` 字典所有 alias（以及本次请求的正则），刷新当前 JSON 筛选对应的
缓存；重新提取后无法计算的 alias 会移除，其他 JSON 筛选的缓存保留：

```bash
python3 main.py --data /path/to/data ipc --force-update
```

缓存损坏时输出 warning 并重新提取；写入失败时输出 warning 并继续绘图。
图与 CSV 都使用所有数据源的 benchmark 交集，缺失项输出 warning。默认生成
`<counter>_comparison.png` 及同名 `.csv`；正则中的文件名特殊字符替换为下划线。
`--output` 可指定图像路径，CSV 使用相同目录和文件名主干。CSV 首列为
`benchmark`，其余列按 `--data` 顺序排列。

图和 CSV 按 SPEC CPU2006 正式顺序排列：先 12 个 INT benchmark，再 17 个 FP
benchmark，最后为 `GEOMEAN`；仅显示实际存在的 benchmark。排序兼容 `gcc`
和 `403.gcc` 等名称，横轴统一显示 `403.gcc` 等带编号的正式名称，CSV 保留原始
benchmark 名称。组间添加竖直分隔线，不显示组名；图例固定在右上角。
非 SPEC06 名称按字母顺序放在 FP 之后、`GEOMEAN` 之前，横轴保留原始名称。

可通过命令行设置坐标轴截断、图片大小及字体：

```bash
python3 main.py --data /path/to/data ipc --y-lim 1 4 --width 14 --height 5 --font-size 16 --legend-font-size 24
```

`--mode value`（默认）绘制缓存中的原始计数器值。`--mode percent` 保留原始比例
值，仅把纵轴和截断标签按百分数显示；例如数据值 `0.5` 显示为 `50%`，CSV
仍保存 `0.5`。`--mode compare` 使用第一个 `--data` 源作为基准，将其显示为
`0%`，其他数据源显示相对变化率 `(value / baseline - 1) × 100%`；GEOMEAN
也使用对应的几何平均比值计算。比较模式的 CSV 保存转换后的百分比，纵轴刻度
带 `%`，纵轴标题带 ` (%)`。比较模式的基准数据源在所有参与绘图的 benchmark
（包括 GEOMEAN）上必须为有限非零值。

`--y-lim MIN MAX` 设置 y 轴上下界；超出范围的柱子截到边界，以三角形标记并
标注原始值（统一保留小数点后两位），包括 `GEOMEAN`。上界截断标注位于柱顶上方，
按从左到右的顺序上下交错，高度间距随字体大小调整。截断只影响图像，CSV
保留原始数值。省略此参数时自动确定坐标轴范围。
`--width` 和 `--height` 单位为英寸（输出为 160 DPI）；默认宽度随 benchmark
数量自动调整，高度为 6 英寸。图表不显示标题。`--font-size` 控制刻度和截断
数值标注，默认 16 点；`--legend-font-size` 单独控制图例和坐标轴标题，默认 24 点。
相同图片宽度及字体设置使用固定的左右边距，纵轴标题到轴线的距离固定；
不同 counter 的刻度文字长度变化时，绘图区的水平位置与宽度保持一致。
`--no-ytitle`、`--no-xtitle`、`--no-legend` 分别隐藏纵轴标题、横轴标题和图例，
可以组合使用。`--no-xlabel` 隐藏横轴 benchmark 刻度标签，`--no-ylabel` 隐藏
纵轴刻度数值及科学计数法倍率；这两个参数保留刻度线，可与前面的开关组合。
例如：

```bash
python3 main.py --data /path/to/data ipc --no-ytitle --no-xtitle --no-legend
python3 main.py --data /path/to/data ipc --no-xlabel --no-ylabel
```

图和 CSV 末尾追加 `GEOMEAN`，分别对每个数据源当前显示的所有 benchmark 值
取等权几何平均（多源使用 benchmark 交集）。计算使用
`exp(Σ(log(value)) / benchmark_count)` 避免连乘溢出；含零时结果为零，负值时报错。

运行测试：

```bash
python3 -m unittest discover -s tests -v
```
