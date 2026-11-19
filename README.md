![DrawScore](assets/logo.jpg)

# 用工程三视图评估生成的 CAD 模型

**简体中文** · [English](README.en.md)

[快速开始](#快速开始) · [评分原理](#评分原理) · [论文结果](#论文结果) · [Python 接口](#python-接口)

图纸要求在倾斜的顶面开两道槽。下图中，候选 A 把两道槽都开到了右侧面，体积交并比（IoU）仍有 **96.85%**。候选 B 把槽开在了正确的面上，只把其中一道槽的轮廓反了过来，IoU 为 **98.23%**。两者的 IoU 只差 1.38 个百分点。再看俯视图和主视图，A 的槽线整段错位，B 的偏差只出现在局部。

![论文图 1。左侧为参考零件，中间为将两道槽开错面的候选 A，右侧为仅反转一道槽轮廓的候选 B。](assets/slot-comparison.png)

**DrawScore 衡量这些线条有多吻合。** 它从两个 STEP 实体生成主视图、俯视图和右视图，分别比较可见线和隐藏线，取三个视图中的最低分。分数范围为 0 到 1，越高表示图纸越吻合。图中的 A 得 **0.5475**，B 得 **0.9625**。

## 快速开始

使用 **Python 3.12**。DrawScore 在 CPU 上运行，通过 build123d 调用 OpenCascade 处理几何，并使用 Open3D 配准模型。以下安装命令适用于 Bash。

```bash
git clone https://github.com/UESTC-Simon/DrawScore.git
cd DrawScore
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

在 Debian 或 Ubuntu 上，如果导入 Open3D 时提示缺少 OpenGL 或 OpenMP 运行库，可运行 `sudo apt-get install libgl1 libgomp1`。

将两个文件名换成自己的 STEP 文件，第一个是参考模型，第二个是待评估模型。两个文件都应包含实体，并采用相同的物理尺度。DrawScore 会旋转、平移候选模型，使其与参考模型对齐。

```bash
drawscore reference.step candidate.step
```

命令向标准输出打印一个保留十位小数的分数。也可以用 `python -m drawscore` 加相同参数运行。评分失败时，程序将原因写入标准错误，并以非零状态码退出。

## 评分原理

1. **对齐两个实体。** 在两个模型的表面采样，用主成分分析估计候选姿态，再用多尺度点到平面 ICP 细化配准。旋转后的零件可能仍有少量配准误差。
2. **生成三个方向的线图。** 两个模型共用相机。几何内核在主视、俯视和右视方向提取可见线与隐藏线，并在每个标准方向附近选取八个角度采样。
3. **分别计算漏画和多画的线。** 沿参考曲线按弧长采样，寻找候选图中最近的同类线条，可见线匹配可见线，隐藏线匹配隐藏线。距离越远，按高斯函数得到的匹配分越低。交换参考图和候选图再算一遍，就能同时衡量参考线条的缺失和候选图中多出的线条。
4. **取吻合度最低的视图。** 每个视图先对八个角度的精确率和召回率分别取平均，再求两者的调和平均。若右视图上的孔位偏了，即使主视图和俯视图都吻合，右视图的误差也会体现在总分里。

### 容差与超时

`--tolerance` 指定高斯距离尺度占参考模型包围盒对角线的比例，默认值为 `0.005`。例如，对角线长 100 mm 时，对应的距离尺度是 0.5 mm。两条线相距 0.5 mm 时，该采样点仍能得到约 61% 的匹配分。容差须大于 0 且小于 1。取值越小，匹配越严格，曲线采样越密，视角采样范围也越窄。

默认超时为 60 秒。复杂模型可以延长到 180 秒。

```bash
drawscore reference.step candidate.step --tolerance 0.005 --timeout 180
```

## 论文结果

| 研究问题 | 实验观察 |
| --- | --- |
| 高 IoU 的模型中，有多少在图纸上仍有偏差？ | 六种 CAD 生成配置共得到 1,188 个有效输出。其中 1,007 个的 IoU ≥ 0.95，这组中有 **190 个（18.9%）** 的 DrawScore 低于 0.95（图 5）。 |
| 用 DrawScore 作训练奖励，生成结果有何变化？ | 在 153 个留出的 Fusion360 模型上，以监督微调模型为起点，用 DrawScore 作奖励进行强化学习后，有效输出比例从 **75.2%** 升至 **89.5%**（表 2）。 |
| 能否根据输入图纸挑选生成结果？ | 在 20 个 BenchCAD 案例上，每例生成 8 个候选，以参考实体投影出的矢量图模拟输入图纸。按 DrawScore 选出最吻合的候选后，平均表面 F-score 为 **0.555**，高于所有有效候选的平均值 **0.529**（表 3）。 |

## Python 接口

```python
from drawscore import score_drawscore

if __name__ == "__main__":
    score = score_drawscore("reference.step", "candidate.step", tolerance=0.005)
    print(f"{score:.10f}")
```

在脚本中请保留 `if __name__ == "__main__":`。评分在独立进程中执行，以便在超时时终止几何运算。可以通过 `timeout` 指定秒数。

## 开发与测试

使用 uv 安装锁定版本的开发依赖并运行检查。

```bash
uv sync --locked
uv run pytest
uv run ruff check .
uv run black --check .
uv run pyright
```

## 许可证与致谢

DrawScore 采用 [Apache License 2.0](LICENSE)。刚体配准和表面采样的部分代码改编自 [CADGenBench](https://github.com/huggingface/cadgenbench)，对应的上游版本和改编说明见 [NOTICE](NOTICE)。
