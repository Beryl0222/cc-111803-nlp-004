# 新闻更正对人物与事件叙述的回查

编辑部在把通讯稿和采访摘句编入稿件时逐条核签。资料中的摘句由记者登记原稿版次和位置。网站快讯接受一个来源，向合作方供稿时要有两个独立来源。同一家编辑台的稿件按一个来源计；通讯社原稿和转载它的合作站即使稿号不同，只要 `origin` 相同也只算一家。具名引语还要校对原文和说话人：摘句若截掉否定词或条件，不能当作相反意思使用。当次刊发回执记录实际采用的稿件内容和核签结果。

编辑桌也可以接收中文更正文和修订稿（`corrections`）：先定位更正指向哪次事件、哪条陈述、哪段原文，再由编辑确认归属，然后分别看它如何影响待发稿和已发稿。

* 一段转述可拆成多个 `facts`，每个事实单独继承摘句。更正只影响被点中的事实，同一版的其余事实仍可继续审；受影响事实先标为“留待核查”，此时整版不能刊发。
* 定位时同时给出候选原文、得分依据和疑点。多个事件、不同日期、不同说话者都可能对应同一条更正，或者更正文字直接改变了否定、条件时，状态为 `needs_editor`，需由编辑选候选或直接填写坐标；未确认归属前不会改动原文，也不会凭稿号或标题把整份旧稿判为失效。
* 撤回或补充只改变证据数量，不改变原句说法：`preview` 显示各栏目在撤回后是否仍够数。常见情形是同一句话仍够网站快讯、却不够供稿。`support` 区分其余证据支撑的是原说法（`original`）、较窄说法（`narrower`）还是只支持说话者身份（`identity`）。
* 更正、说明改动了事实或引语本身，推定推翻当前核签：待发稿留待核查；已发稿按原栏目进入更正工作（`correction_columns` 可指定栏目名）。供稿的原稿回执与更正回执一并存档。
* 只修手误、且改动只是个别字、事实、引语意义和说话者都未变时，沿用原核实结论，并在 `recheck` 中同时保留新旧原文与坐标。若“手误”动到数字、日期、否定、条件或人名，仍按实质更正处理。
* 定格版的每一次改写都追加新版次，旧版次不被覆盖；`coordinates` 同时给出摘句登记的旧坐标和映射到最新版次后的当前位置。
* 同一原文不同片段的修订分别生效。若两条通知落到同一片段，后一条登记为“等待决定”（`awaiting_decision`），不阻塞其他版面，其他审稿工作照常进行。

## 环境

需要 Python 3.11 或更新版本。运行程序仅使用 Python 标准库。测试依赖固定为 pytest 8.3.4，可执行 `python -m pip install -r requirements-dev.txt` 安装。

## 运行

在项目根目录运行 `PYTHONPATH=src python -m nlp_newsroom_correction_chain.desk_cli review flash-41` 查看快讯核签，或运行 `PYTHONPATH=src python -m nlp_newsroom_correction_chain.desk_cli receipt 20261118-feed` 查看供稿回执。编辑桌使用 `fixtures/desk.json` 演练资料。`edition` 是原稿版次，`start` 和 `end` 是原文中的 Unicode 字符偏移，右端不包含在内。`occurred_at` 可选，用来把“12 日”“当晚”这类相对时间放回具体事件；没有时依次取片段日期、原稿日期和稿号。`origin` 可选，标记同源通讯稿。`publication_log` 列出要用这份资料复核的刊发批次。程序在运行期间生成所查回执。

更正工作台用 `fixtures/desk-corrections.json` 演练，包含一条可直接归属的更正、一条撤回、一条日期更正、一条只修个别字的手误、一条同时牵涉停运范围与受访者的模糊更正，以及一条完全无关的气象稿（用来看未确认归属的通知会不会牵连其他版面）：

```
PYTHONPATH=src python -m nlp_newsroom_correction_chain.desk_cli --desk fixtures/desk-corrections.json notice notice-1
PYTHONPATH=src python -m nlp_newsroom_correction_chain.desk_cli --desk fixtures/desk-corrections.json preview notice-2
PYTHONPATH=src python -m nlp_newsroom_correction_chain.desk_cli --desk fixtures/desk-corrections.json apply notice-5
PYTHONPATH=src python -m nlp_newsroom_correction_chain.desk_cli --desk fixtures/desk-corrections.json attribute notice-4 dock-17@1:0-21
PYTHONPATH=src python -m nlp_newsroom_correction_chain.desk_cli --desk fixtures/desk-corrections.json queue
PYTHONPATH=src python -m nlp_newsroom_correction_chain.desk_cli --desk fixtures/desk-corrections.json pending
```

`notice` 查看候选与疑点，`preview` 看改稿前各栏目还剩什么支撑，`attribute` 由编辑指定候选，`apply` 适用更正（`--replacement`、`--claim`、`--fact` 可在归属未定时补充），`coordinates` 回查某条稿件全部摘句的新旧坐标。`queue`/`unresolved` 分别列出已进入更正工作与等待处理的通知，`pending` 列出因归属未定而暂不能刊发的版面。

从项目根目录执行 `PYTHONPATH=src python -m nlp_newsroom_correction_chain.cli ingest fixtures/records.json` 导入样例，随后执行 `PYTHONPATH=src python -m nlp_newsroom_correction_chain.cli list` 查看记录。工作数据写入 `var/records.json`。业务查询使用 fixtures/business.json 中的初始资料，约定见 fixtures/business-contract.md。执行 PYTHONPATH=src python -m nlp_newsroom_correction_chain.cli business 可查看当前业务目录结果。

样例为本地合成文本。记录包含来源、修订号和原文，当前工作区对空白做统一处理，并提供字符及英文词项切分。

## 测试

执行 `python -m pytest -q` 检查摘句校对、供稿核签、刊发回执和更正工作台，并检查资料归档。
