# Ungen

chisel 编译成 verilog 会生成很多 _GEN 信号，不便调试，本工具用于将指定信号中的 _GEN 信号展开成波形可见信号组成的表达式

## 参数列表

`python3 main.py [options] <verilog_file>`

- `-s <signal>, --signal <signal>`：需要展开的信号名称（仅适用于 wire）
- `-l <line>, --line <line>`：信号赋值所在行号（wire 和 reg 均可）（即 `name <= ...` 或 `assign name = ...` 所在行号）
- `-k <id[,ids]>`, `--keep <id[,ids]>`：保留指定的 _GEN id，多个 id 用逗号分隔，如 `-k 0,2,5`，默认全部展开（不保留）
