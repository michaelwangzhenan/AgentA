# 1. 协程是什么

协程（coroutine）是一种可以暂停、之后再续上的函数。Python 里用 async def 定义，用 await 调用。

和普通函数对比：

| | 普通函数 def | 协程函数 async def |
|--|-------------|-------------------|
| 调用 | func() 立刻执行到底 | coro() 只返回协程对象，要交给事件循环跑 |
| 等待 I/O | 占住当前线程傻等 | await 时让出执行权，事件循环可以去跑别的协程 |
| 跑在哪 | 调用它的那条线程 | 默认在事件循环那条线程上 |

一句话：协程适合 I/O 多、要同时照看很多连接的场景（例如 SSE 推流、等队列、查断连），用协作式切换代替「一线程一等到底」。

---

# 2. 最小例子

下面模拟两件事同时进行：一边每隔 0.5 秒打印 A，一边每隔 0.7 秒打印 B。若写成两个普通循环串行，总耗时会叠加；用协程并发，时间会重叠。

```python
import asyncio


async def ticker(name: str, interval: float, count: int) -> None:
    for i in range(count):
        print(f"{name} #{i}")
        await asyncio.sleep(interval)  # 假装在等 I/O：暂停本协程，不堵死事件循环


async def main() -> None:
    # 两个协程同时跑
    await asyncio.gather(
        ticker("A", 0.5, 3),
        ticker("B", 0.7, 3),
    )


if __name__ == "__main__":
    asyncio.run(main())
```

运行（在项目 .venv 下）：

```powershell
.\.venv\Scripts\python.exe docs\knowledge\coroutine\demo_ticker.py
```

把上面代码存成 demo_ticker.py 后执行。预期：A、B 的打印交错出现，总时长约 1.4 秒（取较慢那路），而不是 0.5×3 + 0.7×3。

要点：

- async def 定义协程函数
- await asyncio.sleep(...) 表示「这段时间我先让出，别占着循环」
- asyncio.gather 让多个协程并行推进
- asyncio.run(main()) 启动事件循环，跑完 main 后退出

---

# 3. 和 AgentA 的关系（对照）

AgentA Web 后端每个 uvicorn worker 里有一个 asyncio 事件循环。流式聊天 chat_stream 是 async def，里面再起多个协程分工：

- _event_gen：await queue.get()，从队列取 SSE 事件推给浏览器
- _watch_disconnect：await asyncio.sleep(0.2)，周期性查客户端是否断开
- _drive_agent：await run_in_executor(...)，等线程池里的 agent.run 跑完

agent.run 本身是同步阻塞的（LLM、工具、DB），放在线程池；协程只负责轻量编排和对接前端。详见 docs/code.md 第 5 节。

---

# 4. 常见误区

| 误解 | 实际 |
|------|------|
| async = 多线程 | 默认多条协程共用一条线程 |
| 加了 async 就不会卡 | 协程里若直接调阻塞函数（如 agent.run），仍会卡死事件循环 |
| await = 开新线程 | await 只是暂停当前协程，线程没变 |

阻塞活应显式丢进线程池，例如：

```python
await loop.run_in_executor(None, sync_blocking_fn)
# 或 Python 3.9+
await asyncio.to_thread(sync_blocking_fn)
```
