"""协程最小示例：两个 ticker 并发打印。见同目录 coroutine.md。"""
import asyncio


async def ticker(name: str, interval: float, count: int) -> None:
    for i in range(count):
        print(f"{name} #{i}")
        await asyncio.sleep(interval)


async def main() -> None:
    await asyncio.gather(
        ticker("A", 0.5, 3),
        ticker("B", 0.7, 3),
    )


if __name__ == "__main__":
    asyncio.run(main())
