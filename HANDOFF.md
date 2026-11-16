# 兼容证书与获证任务交接说明

本次在不改写 SDK1 编解码（`vendor/sdk1.py`、`vendor/codec.py`）和解析规则
（`warehouse/legacy.py`）的前提下，为归档闭环新增了**精确版本对兼容证书**和
**消费证书的获证任务入口**。原有接口、表结构中原有部分、`tests/` 下全部原测试
保持不变并继续通过。

## 1. 交付物与运行清单

| 文件 | 作用 |
| --- | --- |
| `warehouse/certification.py` | 全值兼容判定引擎（空值分析、可构造性、共归纳安全关系、有限见证生成） |
| `warehouse/certified.py` | `Certificates`（certify / get_certificate）与 `CertifiedJobs`（create_certified_job） |
| `warehouse/db.py` | 新增 `certificates` 表；原有 DDL 与触发器不动 |
| `warehouse/__init__.py` | `Warehouse` 门面新增三个方法，以及 `certificates`、`certified_jobs` 组件 |
| `enumeration_check.py` | 独立的有限小例枚举对照与递归终止探针（不是判定算法本身） |
| `run_certification.py` | 串联真实检查/演示的小型包装入口，失败按真实退出码终止 |
| `.pointwise-runtime.json` | `verify`、`demo` 各一个 argv 数组，仍为 `version=1`、`kind=cli` |
| `HANDOFF.md` | 本文件 |

运行：

```sh
python3.11 -B run_certification.py --verify   # 原回归 + 功能核验 + 源探针 + 枚举对照
python3.11 -B run_certification.py --demo     # 原仓库演示 + 证书/获证任务演示
```

`--verify` 实际依次执行：`tests/` 全量 unittest（25 个）、`certification_checks`
（7 个）、`certification_examples --check-source`、`enumeration_check.py
--iterations 800 --strict`。包装器只调用真实子进程，从不只打印成功。

## 2. 证书语义

证书对象恰好含四个键：

```python
{"writer_id": int, "reader_id": int, "safe": bool, "witness": value}
```

- `writer_id`、`reader_id` 都是 `register_schema` 返回的**不可变版本 ID**，
  按 ID 精确持久化，不按记录名查找；同名新版本是新 ID，借不到旧证书。
- `safe` 为真当且仅当：写模式能被原 SDK1 编码的**每一个有限 JSON 兼容值**，
  经原 `vendor.sdk1.dumps` 编码后都能被原 `warehouse.legacy.resolve` 按读模式
  成功解析。写模式不存在有限可编码值时 `safe=True`、`witness=None`。
- 不安全证书的 `witness` 是一个完整输入值，落库前会真实地走一遍
  `sdk1.dumps`（必须能编码）和 `resolve`（必须抛 `ResolutionError`）。
  见证本身可以是 `None`（例如写 `null` 读 `long`），因此不能用 witness 是否
  为空代替 `safe`。
- 重复 `certify` 返回已存证书；重开数据库后见证不变。每次返回的都是新对象，
  调用方修改不影响已存内容。
- 首次发布在一个事务内完成，并在提交前触发 `db.hit("certify.before_commit")`；
  边界抛非业务异常时原样传播、不留证书，之后可用同参数重试。

## 3. 判定引擎为什么是“全值精确”，而不是样本/定深枚举

引擎只在编译后的**有限模式节点图**上做不动点计算，从不枚举输入值，也没有
固定深度截断。三个核心概念：

1. **空值分析 `empty(node)`（最小不动点）**
   - 原语永非空；数组永非空（空列表 `[]` 总是可编码，因此
     `array(long)` 对 `array(string)` 即便没有元素也不安全）；
   - 记录在任一字段为空时为空；联合在所有分支为空时为空。
   - 纯递归记录（如 `Loop{next: Loop}`）没有任何有限值，被判为空，
     于是对任意读模式都安全——这与 CERTIFICATION.md 的“无有限值时安全”一致。

2. **带规避集合的可构造性 `EC(node, avoided)`**
   判断 `node` 是否存在一个有限可编码值，使 `avoided` 里的每个写节点都拒绝它。
   这把 SDK1 联合“**首个精确校验成功分支优先**”的路由规则精确内化：
   走第 i 个写联合分支的值，必须被前 i-1 个分支全部拒绝（规避约束）。
   因此无需枚举具体值就能判断某个分支是否真正可达、以及能否构造出失败输入。
   记录字段间的相关性通过“每个规避记录必须有一个字段拒绝”的赋值回溯处理，
   不会把字段独立化而把安全情况误判成不安全。

3. **共归纳安全关系 `OK(writer, avoided, reader)`（最大不动点）**
   - 写联合：对每个真正可达的分支（用 EC 判定可达性）都要求安全，
     完整值选哪个分支就检查哪个分支；
   - 读联合：直接复用原 `legacy.matches` 的**首匹配**，选中后字段解析失败
     不会回退到后续分支（与原解析器逐字一致）；
   - 记录：按读字段顺序解析，同名字段递归，缺失字段用读默认值
     （默认值在注册时已由 `default_wire` 校验，且不经写模式解析），
     无默认值则报 `missing reader field`；
   - 数组：逐元素，空数组不会因“没有元素”而凭空兼容；
   - 递归：再次访问同一节点对时按共归纳闭包视为安全——这正是原解析器对
     递归模式的收敛方式。

`empty` 和 `EC` 是有限状态（节点 × 规避子集）上的最小不动点，`OK` 是同一有限
状态空间上的最大不动点。判定对任意合法有限模式都终止，**既不用固定深度或当前
批次样本代替判定，也不会保守地把安全情况判成不安全**。机器内存、Python 递归栈
等运行资源耗尽不属于模式不兼容（规范明确排除）；判定本身不依赖深度。

### 见证的有限性

不安全结论由 `EC` 的推导产生一个具体有限输入。见证生成对生产式递归做结构化
闭包：再次进入同一记录形状时改用该节点最小的有限可编码值（原语取代表值、
数组取 `[]`、联合优先选叶子分支）。一个只有记录环、没有任何原语/数组出口的节点
必然为空，根本不会进入见证生成，因此生成的见证总是有限且可被 SDK1 编码。
落库前再用真实 `dumps` + `resolve` 复验，任何见证都“真能编码且真触发
`ResolutionError`”。

## 4. 获证任务入口如何取得并消费固定版本身份

`create_certified_job(job_id, batch_ids, reader_id, request_id)`：

1. 入参与 `create_job` 同构；`batch_ids` 有序、可重复、可为空。
2. 单事务（沿用 `enqueue.before_commit` 边界）内先确认读版本和**每个被引用批次**
   都存在，未知者抛 `NotFound`。
3. 从 `batches` 表读取每个**实际批次**固定的 `schema_id`（写版本身份在发布时
   就钉死，不靠名字也不靠“最新模式”）。空批次同样带写版本身份，照样检查；
   空输入列表没有写版本需要获证。
4. 对每个 `(批次写版本, 读版本)` 查证书：缺证或 `safe=False` 一律抛
   `ValueError`，**不自动生成证书，也不用批次实际值代替模式承诺**。
5. 检查、任务绑定、请求回执原子提交；任何失败都不留下新任务或回执。
6. 回执写在独立的 `certified_job` 操作域，复用原请求重放与 Conflict 规则；
   普通 `job` 域的成功请求不能绕过获证检查。两个入口共享 `jobs` 表的
   `job_id` 身份空间（同名占用会 Conflict）。成功请求重放（含任务已完成后）
   返回原 `job_id`；异内容重放或占用 `job_id` 抛 `Conflict`。
7. 获证任务与普通任务共用同一张 `jobs` 表、同一个 `jobs.binding`、
   `run_job` 和 `get_result`，没有第二套执行器；`create_job` 的旧行为完全不变，
   旧任务不被强迫获证。关闭重开后证书、固定绑定、回执、结果均保持不变。

## 5. 独立核验与终止依据

- `certification_examples.py` 的 14 个有限探针全部由判定引擎给出与文档一致的
  结论，且每个不安全见证都经真实编解码/解析回放验证。
- `enumeration_check.py` 独立随机生成合法模式（含自递归、互递归、数组、联合、
  记录），在显式深度/宽度上限内枚举具体值，只用**原** `sdk1.dumps` 过滤可编码
  值、用**原** `resolve` 求真实成败，再与全值判定对照。枚举只能证明“不安全”，
  其“安全”只是有界结果；800 组随机对零误报、零坏见证、零崩溃。
- 同文件的递归终止探针覆盖自联合递归、互递归、递归数组对原语等场景。
- 终止依据：所有判定状态都属于有限集合——`empty` 在有限节点集上做最小不动点；
  `EC` 的状态是 `(节点, 规避节点子集)`，规避节点只来自写模式联合分支/记录字段，
  数量有限；`OK` 的状态是 `(写节点, 规避子集, 读节点)`，同样有限，并以
  “重访即安全”做共归纳闭包。因此对合法有限模式必然终止，与输入值的大小无关。

## 6. 不做的事

不要求最小见证；不迁移 SDK2；不做并发调度、证书撤销、在线模式切换或新增模式
类型；不修改任何原 SDK 或解析规则来让证书成立。
