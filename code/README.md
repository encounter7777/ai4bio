# Struct2SL 复现与结构特征消融

## 数据和协议

使用作者发布的 SynLethDB 2.0 处理后特征及 `Human_SL_ff.csv`。该文件以制表符分隔，过滤掉来源含 `Computational Prediction` 的记录，并保留空来源。上游入口把过滤表路径写为 `Human_SL_filtered.csv`。仓库随附的 SynLethDB 3.0 明细表属于另一份数据。 [Struct2SL 源码](https://github.com/hyr-hit/Struct2SL/blob/main/Struct2SL.py) · [作者数据说明](https://doi.org/10.6084/m9.figshare.33439123.v2)

本地数据审计得到 17,180 个有完整特征的基因，单基因的结构、序列和 PPI 分别为 128、1024、128 维。按基因符号排序规范化无序对。过滤表的 26,699 对中有 23,749 对拥有完整特征。完整 SL 与 non-SL 表交集有 98 对，其中 94 对具有完整特征，81 对也在过滤后的正例中。排除冲突后有 23,668 个正例及 2,651 个符合条件的已报告 non-SL。固定采样补足 21,017 个假定负样本。假定负样本中可能混有尚未发现的 SL 对。

两种模型共用一份 1:1 样本池。基因 A 和 B 分别按结构、序列、PPI 拼接，完整模型为 2560 维。消融去除两侧结构列，保留其余列的原始顺序，共 2304 维。外层使用无分层的 20% 随机测试划分，剩余数据使用洗牌五折，划分与采样种子均为 42。折编号表示该对在哪一折用于验证，0 表示共同外层测试集。

复用上游 MLP 类与默认初始化。保留隐藏层 256、128、64、Dropout 0.5、Adam 学习率 0.004、批大小 64、显式参数范数惩罚、梯度裁剪、验证损失调度与早停。每折独立拟合训练集 StandardScaler。模型训练将 logits 交给 BCEWithLogitsLoss，预测阶段应用 sigmoid。按最低验证损失复制并保存该轮参数，早停耐心 15，正式上限 10,000 轮。每折选定模型后才预测测试集。 [Struct2SL 源码](https://github.com/hyr-hit/Struct2SL/blob/main/Struct2SL.py)

## HPC 运行

将 `code/` 和 `data/` 并列上传到 `/home_data/home/sist/weifs2024/Codes/fsz/ai4bio/`。从 `ai4bio/code/` 执行下列命令并提交 PBS。容器将 `ai4bio/` 挂载到 `/mnt`，从 `/mnt/code` 运行。数据目录应包含 `data/Struct2SL_process_data/Struct2SL/` 下的原始数据文件，包括嵌套的 `struct_features.zip`。项目读取作者发布的 pickle 特征文件，请使用随项目附带的可信副本。

在能访问 Docker Hub、GitHub 和 PyPI 的节点执行一次环境搭建。脚本下载本地 SIF、安装 Python 依赖、检出上游并记录 commit、镜像 SHA-256 和依赖清单。重建时可通过 `UPSTREAM_REVISION=<已记录的完整提交号>` 固定上游版本。PyTorch 使用 CUDA 11.8，计算节点需要可用的 NVIDIA 驱动和 Singularity/Apptainer GPU 透传。

```bash
cd /home_data/home/sist/weifs2024/Codes/fsz/ai4bio/code
bash environment/setup.sh
source environment/runtime.sh
USE_GPU=0 container python src/prepare.py
qsub -v STAGE=smoke project_ai4bio.pbs
```

检查 `outputs/smoke/<PBS_JOBID>/results/comparison/summary.csv` 生成后，再提交正式实验。

```bash
qsub -v STAGE=train project_ai4bio.pbs
```

PBS 每次申请一张 V100、8 核 CPU、64 GB 内存及 30 小时。每个作业顺序运行两个版本各五折。Smoke 使用同一份数据，每折仅训练一轮，输出独立目录。相同快照直接复用，准备脚本会拒绝覆盖已有快照。

## 回传

- `../data/processed/struct2sl/` 下的清单、样本表、划分表、冲突表和特征清单。完整复现可加带 `gene_features.npz`。
- `environment/upstream-revision.txt`、`environment/image.sha256`、`environment/resolved-packages.txt`。
- `outputs/train/<PBS_JOBID>/` 整个目录，含硬件记录、运行清单、十折检查点、缩放参数、训练日志、逐样本预测及比较结果。

`comparison/` 汇总 AUROC、sklearn AP、F1@0.5、测试集正负计数、逐折完整减消融差值以及均值和标准差（ddof=0）。五个折模型共用同一个外层测试集，基因可跨划分重复出现。论文 Table 3 的 Struct2SL 分数为 AUROC 0.9812、AUPR 0.9798、F1 0.9399。撰写报告时应结合冲突排除、负样本去重、折内缩放和验证集选模解释协议差异。 [Huang 等，Table 3](https://pmc.ncbi.nlm.nih.gov/articles/PMC12800370/#tbl3)
