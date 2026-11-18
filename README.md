# 新闻更正对人物与事件叙述的回查

编辑部在把通讯稿和采访摘句编入稿件时逐条核签。资料中的摘句由记者登记原稿版次和位置。网站快讯接受一个来源，向合作方供稿时要有两个独立来源。同一家编辑台的稿件按一个来源计。具名引语还要校对原文和说话人。当次刊发回执记录实际采用的稿件内容和核签结果。

## 环境

需要 Python 3.11 或更新版本。运行程序仅使用 Python 标准库。测试依赖固定为 pytest 8.3.4，可执行 `python -m pip install -r requirements-dev.txt` 安装。

## 运行

在项目根目录运行 `PYTHONPATH=src python -m nlp_newsroom_correction_chain.desk_cli review flash-41` 查看快讯核签，或运行 `PYTHONPATH=src python -m nlp_newsroom_correction_chain.desk_cli receipt 20261118-feed` 查看供稿回执。编辑桌使用 `fixtures/desk.json` 演练资料。`edition` 是原稿版次，`start` 和 `end` 是原文中的 Unicode 字符偏移，右端不包含在内。`publication_log` 列出要用这份资料复核的刊发批次。程序在运行期间生成所查回执。

从项目根目录执行 `PYTHONPATH=src python -m nlp_newsroom_correction_chain.cli ingest fixtures/records.json` 导入样例，随后执行 `PYTHONPATH=src python -m nlp_newsroom_correction_chain.cli list` 查看记录。工作数据写入 `var/records.json`。业务查询使用 fixtures/business.json 中的初始资料，约定见 fixtures/business-contract.md。执行 PYTHONPATH=src python -m nlp_newsroom_correction_chain.cli business 可查看当前业务目录结果。

样例为本地合成文本。记录包含来源、修订号和原文，当前工作区对空白做统一处理，并提供字符及英文词项切分。

## 测试

执行 `python -m pytest -q` 检查摘句校对、供稿核签和刊发回执，并检查资料归档。
