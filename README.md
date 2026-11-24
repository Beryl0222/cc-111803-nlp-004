# 新闻更正对人物与事件叙述的回查

编辑部在把通讯稿和采访摘句编入稿件时逐条核签。资料中的摘句由记者登记原稿版次和位置。网站快讯接受一个来源，向合作方供稿时要有两个独立来源。同一家编辑台的稿件按一个来源计。具名引语还要校对原文和说话人。当次刊发回执记录实际采用的稿件内容和核签结果。

收到中文更正文或修订稿后，编辑桌先定位这条通知改的是哪次事件、哪条陈述，再检查依赖它的短讯、专题标题和向合作方发送的稿件：

- 通知可用稿号、版次、起止坐标，也可只用引文、地点、事件、相对时间或简称。没有明确坐标时列出候选原文和疑点（说话者不同、跨版次、含条件或否定），由编辑确认归属，不把整份旧稿判作失效。
- 一段转述里若有两个事实，按陈述分别继承各自摘句的依据。提到相同地点的人未必说过同一句话；带条件或否定的发言不能被裁成相反意思。
- 某条证据撤回后按剩余证据重新核签：同一句转述可能仍够网站使用，却不够供稿。其余证据是支撑原说法、较窄说法，还是只支撑说话者身份，会分别报出。
- 更正只修手误而事实、引语意义和实际说话者都未变时，可沿用核实结论；新旧坐标都保留可查。改的是事实、引语或说话者时，待发稿不能照旧进入渠道，已发稿进入相应栏目的更正工作。
- 对同一原文不同片段的修订分别生效；针对同一片段的两条通知互相冲突时，值班编辑可先处理不受影响的版面，并看见哪些更正在等决定。
- 合作方收到的旧稿和更正回执都留在回执里，可登记对方确认。

## 环境

需要 Python 3.11 或更新版本。运行程序仅使用 Python 标准库。测试依赖固定为 pytest 8.3.4，可执行 `python -m pip install -r requirements-dev.txt` 安装。

## 运行

在项目根目录运行 `PYTHONPATH=src python -m nlp_newsroom_correction_chain.desk_cli review flash-41` 查看快讯核签，或运行 `PYTHONPATH=src python -m nlp_newsroom_correction_chain.desk_cli receipt 20261118-feed` 查看供稿回执。编辑桌使用 `fixtures/desk.json` 演练资料，更正场景使用 `fixtures/corrections.json`；用 `--desk fixtures/corrections.json` 切换。`edition` 是原稿版次，`start` 和 `end` 是原文中的 Unicode 字符偏移，右端不包含在内。`publication_log` 列出要用这份资料复核的刊发批次。程序在运行期间生成所查回执。

更正场景的常用命令（`--state` 默认写入 `var/desk-state.json`，保存编辑确认的归属和处理决定）：

```
PYTHONPATH=src python -m nlp_newsroom_correction_chain.desk_cli --desk fixtures/corrections.json pending
PYTHONPATH=src python -m nlp_newsroom_correction_chain.desk_cli --desk fixtures/corrections.json resolve notice-vague
PYTHONPATH=src python -m nlp_newsroom_correction_chain.desk_cli --desk fixtures/corrections.json confirm notice-vague dock-17 1 13 20
PYTHONPATH=src python -m nlp_newsroom_correction_chain.desk_cli --desk fixtures/corrections.json impact notice-typo
PYTHONPATH=src python -m nlp_newsroom_correction_chain.desk_cli --desk fixtures/corrections.json worklist
PYTHONPATH=src python -m nlp_newsroom_correction_chain.desk_cli --desk fixtures/corrections.json deliverable flash-41
PYTHONPATH=src python -m nlp_newsroom_correction_chain.desk_cli --desk fixtures/corrections.json partner-log 沿海合作站
```

`resolve` 列出候选和疑点；`confirm` 记录编辑选定的版次与坐标；`impact` 列出受影响的待发栏目和已发批次；`worklist` 给出已发稿的更正工作及是否被冲突阻塞；`deliverable` 按陈述分清可继续进入渠道的部分和待核查的部分；`partner-log` 汇总合作方的旧稿与更正回执。`dismiss NOTICE --reason ...` 用于值班编辑撤下误发或已处理的冲突通知。

从项目根目录执行 `PYTHONPATH=src python -m nlp_newsroom_correction_chain.cli ingest fixtures/records.json` 导入样例，随后执行 `PYTHONPATH=src python -m nlp_newsroom_correction_chain.cli list` 查看记录。工作数据写入 `var/records.json`。业务查询使用 fixtures/business.json 中的初始资料，约定见 fixtures/business-contract.md。执行 PYTHONPATH=src python -m nlp_newsroom_correction_chain.cli business 可查看当前业务目录结果；`correct` 汇总更正场景的待定通知、冲突和已发稿工作。

样例为本地合成文本。记录包含来源、修订号和原文，当前工作区对空白做统一处理，并提供字符及英文词项切分。

## 测试

执行 `python -m pytest -q` 检查摘句校对、供稿核签、刊发回执和更正链（定位、候选、修订回查、来源独立性、冲突与分栏放行），并检查资料归档。
