# 开发任务需求与计划
## 最终目标
做实验，完成CS290Y Project Guidelines.docx（选择的方向为option1）中要求的任务。


## 任务提示
1. 请你不要尝试一次性去大篇幅地修改代码。
2. 我将根据你的完成情况逐步修改“【任务步骤】”，如果完成的好的步骤，我会在步骤条目后添加特殊标记“【passed】”，完成的不好的步骤，我可能会直接删除该步骤。你只需要处理步骤条目后无特殊标记的步骤。
3. 在完成新任务后, 没有生成命令, 不要生成新的md文件, 不要修改本文件, 仅更新taskREADME.md。

## 注意事项
1. 使用到的模型尽量下载到本地。
2. 运行实验时，尽量给出命令让我去运行，不要私自运行。

## 环境
现在所处环境为本机的windows环境(不能GPU测试)，部署好代码我会将其放置服务器端HPC进行运行，写出pbs脚本使用singularrity环境进行运行，使用hpc集群中V100 GPU进行实验。
pbs运行脚本最大调度资源2个任务，2个GPU, 16核CPU, 内存64G, 时间30h。

### 1 
我会将code放进服务器端的/home_data/home/sist/weifs2024/Codes/fsz/ai4bio/，帮忙按照下面步骤部署项目：
1. 给出搭建singularity环境sh脚本。
2. 按照code\plan.md和code\implementation.md部署代码。
3. 给出pbs提交任务脚本(参考code\project_ai4bio.pbs)。
完成任务后，我会将结果回传，然后后面再根据结果，写report。
code中要写和维护自己的README.md。



