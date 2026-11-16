# 固定版本兼容承诺

本次在现有 SDK1 归档闭环中增加兼容证书及消费证书的获证任务入口。
README.md、CONTRACT.md 的正常接口、输入类型、名称环境、编解码及解析语义继续有效。
vendor/sdk2.py 不是本次迁移目标。全部原文件及 tests 中的原测试随题提供。

## A：精确版本对证书

在 Warehouse 上新增：

- certify(writer_id, reader_id) -> certificate
- get_certificate(writer_id, reader_id) -> certificate 或 None

ID 均指 register_schema 返回的不可变版本，不按模式名称查找。
任一 ID 不存在时，两接口均抛 NotFound。已知版本对尚无证书时查询返回 None。
证书仅含四个键：
`{"writer_id": int, "reader_id": int, "safe": bool, "witness": value}`。

safe 当且仅当该写模式能被 SDK1 编码的每个有限 JSON 兼容输入值，
用原 vendor.sdk1.dumps 编码后，均能由原 warehouse.legacy.resolve 按该读模式成功解析。
若写模式不存在有限可编码值，则 safe 为 True。
安全证书的 witness 为 None；不安全证书的 witness 必须是一个完整输入值，
能经原 SDK1 编码，并使原 resolve 抛 ResolutionError。不要求最小见证。
不安全见证本身可以是 None，不能用 witness 是否为空代替 safe。
这里判断模式解析语义，不把机器内存、Python 递归栈等运行资源耗尽当成模式不兼容。

写联合必须按完整值校验选择首个成功分支；读联合必须使用原 matches 的首匹配，
选中后解析失败不得尝试下一分支。字段、数组、读默认值及递归引用均沿用原契约。
只研究有限输入，允许合法模式没有有限值。
允许全量分析模式，不要求增量或最优复杂度，但判定须对合法有限模式终止，
不能用固定深度或当前批次样本枚举代替精确判定，也不能保守地把安全情况判为不安全。

certify 将结论及见证按确切 writer_id、reader_id 持久保存。
同一版本对重复调用返回已存证书，见证不能随重试或重开改变。
接口返回新对象，调用者修改它不能改变已存证书。
首次证书发布使用事务，并触发 db.hit("certify.before_commit")；
该边界发生非业务异常时原样传播，不留下证书，之后可重试。
不规定表结构、分析表示或代码模块划分；不要改写原 SDK 或解析规则来使证书成立。

## B：获证任务绑定

在 Warehouse 上新增：
`create_certified_job(job_id, batch_ids, reader_id, request_id) -> job_id`。

参数及成功绑定格式与 create_job 相同，输入列表有序且可重复，也可为空。
先确认全部引用存在，未知读版本或批次抛 NotFound。
每个实际批次的固定 schema_id 与 reader_id 必须已有 safe=True 的证书；
缺证或不安全均抛 ValueError，不自动生成证书，不用实际批次值代替模式承诺。
空批次仍须检查写版本；空输入列表没有写版本需要获证。
证书只覆盖确切版本对，同名新版本不能借用旧证书。

获证检查、任务绑定与请求回执原子提交，失败不留下新任务或回执。
沿用 enqueue.before_commit 钩子；非业务异常原样传播并回滚，可用原请求重试。
获证请求在独立的 certified_job 操作域内应用原请求重放与 Conflict 规则；
普通 job 域的成功请求不能绕过获证检查，两入口共享 job_id 身份空间。
成功请求重放返回原 job_id，包括任务已完成后；异内容重放或占用 job_id 抛 Conflict。
create_job 的普通行为保持不变，不得强迫旧任务获证。
获证任务复用原 binding、run_job 和 get_result，不另建一套执行器。
关闭重开后证书、固定绑定、回执和结果保持不变。

## 样例与可执行调用

certification_examples.py 提供独立模式文档及有限探针：
long 到 double 安全；long 到 string 不安全，0 是合法见证。
写联合 [A{x:long}, B{x:long}] 读 A 安全：完整值总由 A 编码。
写联合 [A{x:long}, B{x:[long,string]}] 读 A 不安全，{"x":""} 能进入 B。
写 A 读 [Bad(alias A){y:long}, A] 不安全，{"x":1} 在首匹配内因缺 y 失败。
另外给出数组混合、字段组合、无有限值递归、可终止递归和空数组的样例。
这些有限探针只检查样例的实际编解码行为，不构成全值判定算法。

```python
from warehouse import Warehouse
with Warehouse() as w:
    a = w.register_schema("long", "writer")
    b = w.register_schema("double", "reader")
    assert w.get_certificate(a, b) is None
    assert w.certify(a, b) == {
        "writer_id": a, "reader_id": b, "safe": True, "witness": None}
    w.publish_batch("data", a, [7], "publish")
    w.create_certified_job("checked", ["data", "data"], b, "checked")
    result = w.run_job("checked")
    assert [row["value"] for row in result["rows"]] == [7.0, 7.0]
```

## 验证和演示交付

原完整回归命令保持为：
`python3.11 -B -m unittest discover -s tests -t . -v`。
母体正常样例核对入口：
`python3.11 -B -m certification_examples --check-source`。
本轮公开功能核验：
`python3.11 -B -m unittest -v certification_checks`。
功能演示：
`python3.11 -B -m certification_examples --demo`。

certification_checks.py 核对证书、真实见证回放、版本绑定、空批次、请求重放、
两处提交边界及关闭重开；它不提供兼容判定实现。
补充独立有限小例枚举对照和递归终止依据；有限枚举不能替代全值正确性的解释。
交接说明应说明证书的语义以及任务入口如何取得、消费实际批次的固定版本身份。
不要求最小见证、SDK2 迁移、并发调度、证书撤销、在线模式切换或新增模式类型。

沿现有 .pointwise-runtime.json 更新 verify，使其运行全部原回归及本轮功能核验，
demo 展示证书生成、批次发布、获证创建与原 run_job 的实际结果。
清单继续使用 version=1、kind=cli；setup 是准备命令的 argv 数组列表，
verify 和 demo 各是一个 argv 数组。可使用小型包装入口串联真实检查，不能只打印成功。
