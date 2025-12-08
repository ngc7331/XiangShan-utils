# BpTrace

一个用于 XiangShan 分支预测 trace 的分析工具

## 参数列表

`python3 main.py [options] <dbfile>`

- `-o <file>, --output <file>`：输出的 csv 文件路径，如未指定则使用 `./trace.csv`
- `-s <cycle>, --start <cycle>`：开始处理的时钟周期数，即 STAMP 最小值，默认为 0
- `-e <cycle>, --end <cycle>`：停止处理的时钟周期数，即 STAMP 最大值，默认为处理整个 db
- `-n <num>, --num <num>`：和 `-e` 不能同时使用，处理的分支条目数，即从 `-s` 开始的条目数
- `--only-addr <addr>`：只输出指定地址的条目
- `--only-mispredict`：只输出误预测的条目
- `--only-override`：只输出发生 override（即 s1 和 s3 预测不一致）的条目
- `--brtype`：输出 brType 字段
- `--rasaction`：输出 rasAction 字段
- `--target`：输出 target 字段
- `-m, --meta <name[,names]>`：需要显示的元数据字段名，多个字段用逗号分隔，如 `--meta XXX,YYY`，默认为不显示任何元数据
- `--render-prunedaddr`：将 PrunedAddr 渲染为真实地址
- `--only-stats`：只输出统计信息，不输出 trace
- `--stats-mispredict <top_n>`：输出误预测次数最多的前 n 个预测块（startVAddr），默认为 10
- `--stats-br-mispredict <top_n>`：输出误预测次数最多的前 n 个分支（startVAddr, position）对，默认为 20
- `--stats-type-mispredict <enable>`：输出按属性（brType, rasAction）分类的误预测次数统计，默认为开启
- `dbfile`：输入的数据库文件路径

## 数据库结构

XiangShan 通过 ChiselDB 功能在仿真时保存分支预测的预测和训练数据，存入 sqlite3 数据库中，分为两张表：

- `BpuPredictionTrace`
  - `STAMP`：时间戳（仿真周期数）
  - `PERFMETA_BPID`：预测序列号，唯一
  - `PERFMETA_STARTVADDR_ADDR`：预测块起始地址
  - `PERFMETA_S{1,3}PREDICTION_TAKEN`：s1/3 流水级预测的是否 taken
  - `PERFMETA_S{1,3}PREDICTION_CFIPOSITION`：s1/3 流水级预测的分支位置
  - `PERFMETA_S{1,3}PREDICTION_TARGET_ADDR`：s1/3 流水级预测的分支目标地址
  - `PERFMETA_S{1,3}PREDICTION_ATTRIBUTE_BRANCHTYPE`：s1/3 流水级预测的分支属性之分支类型
  - `PERFMETA_S{1,3}PREDICTION_ATTRIBUTE_RASACTION`：s1/3 流水级预测的分支属性之 RAS 动作
  - `META_XXX`：其他元数据
- `BpuTrainTrace`
  - `STAMP`：时间戳（仿真周期数）
  - `TRAIN_PERFMETA_BPID`：预测序列号，在此表中不保证唯一，因为一个预测块可能被分成多次训练
  - `TRAIN_PERFMETA_STARTVADDR_ADDR`：预测块起始地址
  - `TRAIN_META_XXX`：其他元数据
  - `TRAIN_BRANCHES_{0-7}_VALID`：训练的分支是否有效
  - `TRAIN_BRANCHES_{0-7}_BITS_TAKEN`：训练的分支是否 taken
  - `TRAIN_BRANCHES_{0-7}_BITS_CFIPOSITION`：训练的分支位置
  - `TRAIN_BRANCHES_{0-7}_BITS_TARGET_ADDR`：训练的分支目标地址
  - `TRAIN_BRANCHES_{0-7}_BITS_ATTRIBUTE_BRANCHTYPE`：训练的分支属性之分支类型
  - `TRAIN_BRANCHES_{0-7}_BITS_ATTRIBUTE_RASACTION`：训练的分支属性之 RAS 动作
  - `TRAIN_BRANCHES_{0-7}_BITS_MISPREDICT`：训练是否是误预测引起的

## 输出

输出为 CSV 格式，字段包括：

- `stamp`：时间戳
- `id`：序列号
- `addr`：地址
- `type`：预测（p1/p3）或训练（t0-t7）
- `taken`：是否 taken
- `position`：分支位置
- `mispredict`：若 `type` 为预测则固定为 `-`，否则表示是否误预测
- `brType`：分支类型（若使用 `--brtype` 参数）
- `rasAction`：RAS 动作（若使用 `--rasaction` 参数）
- `target`：目标地址（若使用 `--target` 参数）
- `XXX`：其他元数据字段（若使用 `--meta XXX` 参数）
