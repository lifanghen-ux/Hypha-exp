# Hypha-exp

`Hypha-exp` 是当前服务器上的第一个独立开发与实验项目。

## 开始工作

```bash
ssh ali-root
cd ~/projects/Hypha-exp
source .venv/bin/activate
```

退出虚拟环境：

```bash
deactivate
```

## 目录约定

- `src/`：可复用的正式代码
- `experiments/`：一次性实验、Notebook 和探索性代码
- `configs/`：YAML、JSON 等配置文件
- `scripts/`：训练、评测、数据处理等可重复执行的脚本
- `data/raw/`：原始数据，不直接修改
- `data/processed/`：处理后的中间数据
- `outputs/`：模型、图表和实验产物
- `logs/`：运行日志
- `docs/`：设计记录和实验结论

## 管理原则

1. 源码和配置进入 Git；密钥、`.env`、数据、日志和大型输出不进入 Git。
2. 每个项目使用自己的 `.venv`，避免依赖互相污染。
3. 每次实验在 `experiments/` 下建立独立子目录，并记录配置、日期和结果。
4. 可复用逻辑从 `experiments/` 整理到 `src/`。
