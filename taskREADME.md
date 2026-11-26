# Option 1 部署交付

已提供 Struct2SL 数据审计和准备、完整模型与结构消融训练、配对评价和绘图代码，以及 Singularity 环境脚本和 V100 PBS 脚本。HPC 提交命令与回传清单见 [code/README.md](code/README.md)。

部署目录采用同级的 `code/` 和 `data/`，从服务器的 `ai4bio/code/` 执行环境搭建、数据准备和 PBS 提交命令。

本地核对了作者入口、数据格式、实际特征维度和冲突计数。正式训练和报告留待 HPC 运行并回传结果。

镜像构建临时目录和 OCI 缓存已移到项目盘，支持通过环境变量切换到 scratch。
