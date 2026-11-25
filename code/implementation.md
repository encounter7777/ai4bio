# Option 1 实现说明：Struct2SL 复现与结构特征消融实验

## 1. 实现要求

需要实现两个受控的模型版本：

- `full`：使用作者发布的全部特征的 Struct2SL；
- `no_structure`：从同一模型中移除两个基因对应的结构特征列。

两个版本必须使用完全相同的基因对池、标签、外层测试集、五折训练/验证划分、随机种子、预处理规则、验证集模型选择流程和调参预算。

**不要自行编造仓库中的文件名、特征列名、特征维度、超参数或命令行参数。** 在编写最终配置前，必须从作者代码仓库和处理后数据说明中确认这些信息。

## 2. 首先检查的数据来源

- Struct2SL 代码：https://github.com/hyr-hit/Struct2SL
- Struct2SL 处理后数据：https://figshare.com/articles/dataset/_b_Strcuct2SL_Fresh_reproduction_b_/33439123
- SynLethDB 3.0: https://www.synlethdb.com/download
- 论文：Huang Y, Yuan R, Li Y, Xing Z, Li J. “Struct2SL: synthetic lethality prediction based on AlphaFold2 structure information and multilayer perceptron.” *Computational and Structural Biotechnology Journal*, 2025, 27:1570–1577.

项目开始时，需要检查并记录：

1. 仓库准确的 Git commit/revision 和实际运行入口；
2. 实现外层 20% 测试集和五折划分的脚本；
3. 选用的过滤后 SL 文件及其筛选流程；
4. 处理后特征文件、标识符、数据结构和特征组边界；
5. 负样本候选集合及作者发布的负样本采样流程；
6. 模型结构、初始化方法、优化器、损失函数、训练设置和验证/轮数选择方式；
7. 所需软件版本和环境限制。

应直接使用作者提供的蛋白质特征，无需重新运行 AlphaFold2。

## 3. 建议的项目目录结构

实际目录应根据仓库检查结果调整，不要随意重命名或修改原始数据。

```text
project/
├── README.md
├── plan.md
├── implementation.md
├── configs/
│   ├── data.yaml
│   ├── experiment.yaml
│   └── seeds.yaml
├── data/
│   ├── raw/
│   ├── processed/
│   └── manifests/
│       ├── data_manifest.json
│       ├── sampled_pairs.csv
│       ├── outer_split.csv
│       └── fold_assignments.csv
├── src/
│   ├── inspect_sources.py
│   ├── prepare_pairs.py
│   ├── build_splits.py
│   ├── feature_schema.py
│   ├── dataset.py
│   ├── model.py
│   ├── train.py
│   ├── evaluate.py
│   ├── compare.py
│   └── plotting.py
├── tests/
│   ├── test_pair_integrity.py
│   ├── test_splits.py
│   ├── test_feature_ablation.py
│   └── test_preprocessing.py
├── outputs/
│   ├── full/
│   ├── no_structure/
│   └── comparison/
└── environment/
    └── requirements-or-lockfile
```

## 4. 数据准备

### 4.1 检查原始数据表

编写数据检查脚本，输出文件名、表格维度、列名和类型、缺失值数量、基因数量、基因对数量、标签分布、特征维度以及不同数据表之间的标识符重合情况。将检查结果保存到数据清单中。

从真实数据中确认标识符和特征布局。论文介绍了序列、PPI 和 AlphaFold2 衍生结构信息，但具体列名和编码方式必须以作者发布的数据和代码为准。

### 4.2 将无序基因对规范化

将 `(gene_a, gene_b)` 和 `(gene_b, gene_a)` 视为同一个基因对。按照数据来源所规定的方式规范化基因标识符，并为每个基因对生成确定性的规范键。

概念性示例（实际列名和标识符处理方式需要根据数据调整）：

```python
def canonical_pair(gene_a, gene_b):
    gene_a = normalize_gene_id(gene_a)
    gene_b = normalize_gene_id(gene_b)
    if gene_a == gene_b:
        raise ValueError("Self-pairs are not valid examples")
    return tuple(sorted((gene_a, gene_b)))
```

之后执行：

1. 为规范化后的基因对关联标签；
2. 统计重复记录；
3. 检查是否存在标签相互矛盾的基因对；
4. 采用有数据依据且有记录的处理规则。如果无法合理解决，则排除这些基因对并报告数量和原因；
5. 每个规范化基因对只保留一条记录；
6. 断言每个保留的基因对只有一个标签。

以下为检查思路，实际列名需替换为数据中确认的名称：

```python
assert pair_table["pair_id"].is_unique
assert not pair_table.duplicated(["gene_low", "gene_high"]).any()
assert not pair_table.groupby("pair_id")["label"].nunique().gt(1).any()
```

### 4.3 构建唯一的一份 1:1 负样本池

遵循作者发布的数据构建流程，并记录负样本候选集合的定义。排除自配对、重复无序基因对和已知正样本基因对，并在采样前处理标签冲突。

使用固定随机种子，只采样一次负样本，数量与保留的正样本数量相同。保存采样后的基因对 ID 和最终数据表。两个模型版本及所有折都必须使用相同文件，消融实验不能重新采样。

如果符合条件的候选负样本不足，不要擅自改变采样比例，应停止并报告原因。必须将采样得到的未观测基因对称为**假定负样本**，而非经过验证的非 SL 基因对，因为其中可能包含尚未发现的正样本。

### 4.4 特征结构

使用作者提供的特征表示。建立明确的特征清单，记录：
- 特征组和具体列名；
- Gene A 和 Gene B 各自对应的特征列；
- 是否存在基因对层面的特征；
- 缺失特征的处理方式；
- 作者模型要求的特征顺序。

不要猜测特征名称，也不要使用未经确认的字符串匹配规则。完整特征版本和无结构特征版本的特征清单都应保存并纳入版本管理。

## 5. 数据划分与防止数据泄漏

### 5.1 必需的数据划分

复现作者 Python 脚本中的外层 20% 测试集划分，以及在剩余 80% 数据中进行的五折训练/验证划分。

检查作者发布的代码，确认实际使用的划分函数、是否进行分层抽样以及随机种子。保存所有基因对 ID 和划分分配结果。

除非明确记录偏离之处，否则不要自行替换划分方法。两个模型版本必须读取相同的固定划分文件。

### 5.2 每折独立进行特征缩放

对于每一折：

1. 在外层 80% 数据中确定该折训练集和验证集的 ID；
2. 只使用该折训练数据拟合缩放器及其他需要学习参数的预处理步骤；
3. 使用训练集拟合的变换处理训练集、验证集和共同的外层测试集；
4. 仅使用训练集和验证集训练、选择模型设置和轮数；
5. 使用选定模型在共同的外层测试集上进行最终评价。

禁止在划分数据前对全部数据拟合统一缩放器，也不能将验证集或测试集用于 `fit`。应添加断言或测试，确保缩放器只接收到训练数据。

## 6. 模型实现

### 6.1 完整特征模型

在环境兼容的情况下，优先直接使用作者的实现。尽可能保留发布版本的模型结构和特征定义。

论文介绍的框架将蛋白质特征表示组合起来，并使用多层感知机进行 SL 预测。具体层数、隐藏层维度、激活函数、Dropout、优化器、损失函数和训练设置必须从实际代码中读取，不能自行设定。

如果必须进行适配，应记录所有重要修改，包括模型结构、初始化、预训练和训练流程的差异。如果进行了实质性修改，不应声称是完全一致的复现。

### 6.2 无结构特征模型

从完整特征清单中移除两个基因对应的结构特征列，其他非结构特征保持原有相对顺序。由于输入维度减少，需要单独训练模型。

使用断言确认：
- 两个版本的基因对 ID 和标签完全相同；
- 外层测试集 ID 和五折分配完全相同；
- 消融版本不包含任何结构特征列；
- 剩余特征列的顺序不变；
- 两个版本唯一有意的差异是结构特征的移除及其导致的输入维度变化。

检查思路：

```python
assert full_pair_ids.equals(ablation_pair_ids)
assert full_labels.equals(ablation_labels)
assert full_outer_test_ids.equals(ablation_outer_test_ids)
assert full_fold_assignments.equals(ablation_fold_assignments)
assert not (set(ablation_columns) & set(structural_columns))
```

实际的 `structural_columns` 列表必须根据检查后的数据结构确定。

## 7. 模型训练流程

对于两个版本（`full`、`no_structure`）的每一折，执行：

1. 读取固定的基因对池和数据划分文件；
2. 根据当前模型版本的特征清单组装输入；
3. 只在训练数据上拟合预处理步骤；
4. 按作者发布的实现初始化模型；
5. 使用训练集训练；
6. 使用验证集选择训练轮数和模型设置，并在作者流程包含早停时依据验证集执行早停；
7. 保存选定的模型检查点和验证集选择记录；
8. 对共同的外层测试集生成预测概率；
9. 保存逐样本预测结果，包括基因对 ID 和真实标签。

两个版本必须采用相同的调参预算和模型选择规则。只有模型选择完成后，才能生成测试集预测。

建议的输出目录：

```text
outputs/
├── full/fold_1/{checkpoint,train.log,test_predictions.csv}
├── full/fold_2/...
├── no_structure/fold_1/{checkpoint,train.log,test_predictions.csv}
└── comparison/
```

## 8. 模型评价

### 8.1 必需指标

对五个已训练模型分别在共同的外层测试集上计算：

```python
from sklearn.metrics import roc_auc_score, average_precision_score, f1_score

auroc = roc_auc_score(y_true, y_prob)
ap = average_precision_score(y_true, y_prob)
f1_at_05 = f1_score(y_true, (y_prob >= 0.5).astype(int))
```

AP 必须使用 `average_precision_score`。F1 必须使用固定概率阈值 0.5，不能根据测试集表现调整阈值。同时报告测试集正样本数和负样本数。

### 8.2 汇总和配对比较

分别计算两个模型版本五次结果的均值和标准差。明确说明五个模型都在同一个外层测试集上进行评价，并非五个独立测试集。

配对差值定义为：

`差值 = 完整特征模型指标 - 无结构特征模型指标`

由于折分配和随机种子匹配，将第 1 折与第 1 折配对，以此类推。报告逐折差值及其均值；必要时也报告标准差。

### 8.3 结果表格模板

| 模型 | AUROC（均值 ± 标准差） | AP（均值 ± 标准差） | F1@0.5（均值 ± 标准差） | 测试集正样本数 | 测试集负样本数 |
|---|---:|---:|---:|---:|---:|
| 完整特征 Struct2SL | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 |
| 移除结构特征的 Struct2SL | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 |
| 配对差值（完整 − 无结构） | 待填写 | 待填写 | 待填写 | 同一测试集 | 同一测试集 |

同时提供逐折配对结果：

| 折 | 完整 AUROC | 无结构 AUROC | Δ AUROC | 完整 AP | 无结构 AP | Δ AP | 完整 F1 | 无结构 F1 | Δ F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 |
| 2 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 |
| 3 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 |
| 4 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 |
| 5 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 | 待填写 |

将完整特征模型的复现结果与 Huang 等人的论文 Table 3 作背景比较。解释数据过滤、基因对构建、负样本采样、划分方案、代码实现和运行环境上的差异。不要求数值完全一致。

## 9. 建议的代码接口

以下只是概念性接口，并不代表作者仓库中已经存在这些函数。需要根据实际代码进行调整。

```python
def build_pair_pool(source_files, data_config, seed):
    """构建去重、冲突处理后的唯一 1:1 基因对池，并生成审计报告。"""
    ...

def build_released_protocol_splits(pair_table, split_config, seeds):
    """复现外层 20% 测试集及剩余 80% 中的五折划分。"""
    ...

def get_feature_schemas(source_schema):
    """根据检查后的数据结构返回完整特征和无结构特征列清单。"""
    ...

def train_one_fold(pair_table, split_ids, feature_columns, model_config, seed):
    """仅在训练折拟合预处理；基于验证集训练和选择；保存测试预测。"""
    ...

def evaluate_predictions(prediction_file):
    """计算 AUROC、sklearn AP 和阈值 0.5 下的 F1。"""
    ...

def compare_matched_runs(full_runs, no_structure_runs):
    """计算逐折配对指标差值和汇总结果。"""
    ...
```

## 10. 执行顺序

方括号中的占位内容必须在检查仓库后替换为真实脚本和参数。

```bash
# 克隆代码并记录准确版本
git clone https://github.com/hyr-hit/Struct2SL
cd Struct2SL
git rev-parse HEAD

# 根据仓库说明创建并固定依赖环境。
# 检查脚本、处理后数据、特征定义和数据划分实现。
# 将确认的路径和设置记录到 configs/ 和 data/manifests/。

# 构建、检查并固定唯一的基因对池：
python [已确认的基因对准备脚本] [已确认的参数]

# 复现并保存作者的数据划分方案：
python [已确认的数据划分脚本] [已确认的参数]

# 运行五折完整特征模型：
python [已确认的训练入口] [完整特征模型参数]

# 使用相同基因对和划分文件运行五折无结构特征模型：
python [已确认的训练入口] [无结构特征模型参数]

# 评价并比较配对预测结果：
python [已确认的评价脚本] [已确认的参数]
```

不要直接执行包含方括号的占位命令。必须先将其替换为仓库中经过确认的脚本和参数。如果作者脚本将多个步骤合并在一起，应记录实际调用方式及所有修改。

## 11. 验证清单

### 数据完整性
- [ ] 所有基因对都已规范化为无序基因对；
- [ ] 不存在自配对或重复无序基因对；
- [ ] 不存在保留的标签冲突基因对；
- [ ] 正负样本数量相同；
- [ ] 负样本只采样一次并保存；
- [ ] 将采样得到的未观测基因对称为假定负样本。

### 数据划分与预处理
- [ ] 外层测试集符合作者发布的 20% 划分方案；
- [ ] 剩余 80% 中的五折划分遵循作者代码；
- [ ] 随机种子和基因对 ID 均已保存；
- [ ] 两个模型版本使用完全相同的样本、标签和划分；
- [ ] 每个缩放器只在训练数据上拟合；
- [ ] 模型选择使用验证集，不使用测试集。

### 模型与评价
- [ ] 完整模型遵循已确认的作者特征和模型结构；
- [ ] 无结构模型只移除两个基因对应的结构特征列；
- [ ] 剩余特征顺序不变；
- [ ] 两个版本的调参预算和训练流程一致；
- [ ] 每个版本均训练五个模型；
- [ ] 十个模型都预测同一批外层测试样本；
- [ ] 报告 AUROC、sklearn AP、F1@0.5 和类别数量；
- [ ] 报告均值、标准差和逐折配对差值；
- [ ] 说明五个模型共用同一个测试集；
- [ ] 讨论与论文 Table 3 的比较及所有实现差异。

## 12. 结果解释

结论应严格限定在本实验的数据和评估方案范围内：本实验是否支持所选结构表示具有额外贡献。

- 如果完整特征模型在各折中表现持续更好，可以将结果解释为支持结构特征可能有贡献的证据，但要说明数据和负样本标签的假设。
- 如果两个模型表现相近，应表述为本实验没有证明结构特征列带来了可测量的收益；不能据此断言蛋白质结构普遍没有预测价值。
- 如果无结构特征模型表现更好，应如实报告负面结果，并讨论特征冗余、噪声、特征质量、优化过程以及数据/划分局限。
- 如果不同折之间差异较大，应强调结果波动，不能只根据平均值作出强结论。

需要讨论的潜在局限包括：假定负样本中可能混入真实 SL、已知 SL 注释偏差、数据划分特征、所使用的特定结构表示、特征缺失，以及五个模型共用一个测试集这一事实。

## 13. 可选扩展（非必需）

只有完成必需实验后，才选择至多一个扩展：

1. **基因不相交划分**：明确基因如何分配，以及跨分区基因对如何处理；
2. **不平衡测试集**：例如 1:10，明确候选池、采样方法和最终数量；
3. **额外的已发表模型**：例如 ESM-2 序列特征基线，并定义特征生成和评价方案。

对于排序类扩展，必须定义候选池大小 K 和假定正样本比例。困难候选基因对可以作为未标注干扰项，但共享通路、复合物成员关系、旁系同源或共同必需性不能证明其为负 SL 标签。

## 14. 参考文献

1. Huang Y, Yuan R, Li Y, Xing Z, Li J. Struct2SL: synthetic lethality prediction based on AlphaFold2 structure information and multilayer perceptron. *Computational and Structural Biotechnology Journal*. 2025;27:1570–1577.
2. Feng Y, Long Y, Wang H, et al. Benchmarking machine learning methods for synthetic lethality prediction in cancer. *Nature Communications*. 2024;15:9058.
3. Wang J, Wu M, Huang X, et al. SynLethDB 2.0: a web-based knowledge graph database on synthetic lethality for novel anticancer drug discovery. *Database*. 2022;baac030.
4. Jumper J, Evans R, Pritzel A, et al. Highly accurate protein structure prediction with AlphaFold. *Nature*. 2021;596(7873):583–589.
5. Huang A, Garraway LA, Ashworth A, et al. Synthetic lethality as an engine for cancer drug target discovery. *Nature Reviews Drug Discovery*. 2020;19:23–38.
