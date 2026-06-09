# 1. 文档更新
design
readme
用户手册
代码指南：design中的部分移过来

# 2. 优化
## 2.1. API 可配置
## 2.2. Token 统计（每轮 / 累计）
## 2.3. 用户记忆：不限于固定格式


# 3. 新功能
## 3.1. [新 Feature](iter_7_retro.md#24-选定feature)
## 3.2. 新业务
## 3.3. workflow


# 4. TBD
多语言
skill os


# 5. 待修 bug
## 5.1. AutoGPT Plan 阶段 JSON 解析失败
`autogpt_agent.py::_plan` 直接 `json.loads(raw)`，模型若把 JSON 包在 ```json 代码围栏里就解析失败，
每次都回退成单任务，AutoGPT 的多任务分解失效。修法：解析前剥掉 markdown 代码围栏（再容错取首个 `{...}`）。


