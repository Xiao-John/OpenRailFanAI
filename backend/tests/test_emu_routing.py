"""emu.routing 工具测试（rail.re 交路查询）。"""
from __future__ import annotations

import asyncio

from app.tools import registry


async def test_emu_routing_registered():
    names = {t.name for t in registry.list_enabled()}
    assert "emu.routing" in names, names
    print("[PASS] emu.routing 已注册")


async def test_emu_routing_missing_params():
    r = await registry.invoke_by_name("emu.routing", {})
    assert not r.ok and "缺少参数" in (r.error or ""), r
    print("[PASS] emu.routing 缺参数时优雅报错")


async def test_emu_routing_train():
    """查车次担当车组（需境内网络可达 api.rail.re）。"""
    r = await registry.invoke_by_name("emu.routing", {"train": "G1", "today": True, "limit": 5})
    if r.ok:
        d = r.data
        assert d["kind"] == "train", d
        assert d["records"], d
        print(f"[PASS] emu.routing(train=G1) -> {d['count']} 条记录")
        print(f"       今日担当: {[x['emu_no_display'] for x in d['today_records']] or '未收录'}")
        print(f"       {r.text.splitlines()[0]}")
    else:
        # 网络不可达时应优雅降级（不抛异常）
        print(f"[SKIP] emu.routing(train=G1) 网络不可达: {r.error}")


async def test_emu_routing_emu_no():
    """查车组担当车次。"""
    r = await registry.invoke_by_name("emu.routing", {"emu_no": "CR400AF", "limit": 5})
    if r.ok:
        d = r.data
        assert d["kind"] == "emu", d
        print(f"[PASS] emu.routing(emu_no=CR400AF) -> {d['count']} 条记录")
        print(f"       {r.text.splitlines()[0]}")
    else:
        print(f"[SKIP] emu.routing(emu_no=CR400AF) 网络不可达: {r.error}")


async def main():
    await test_emu_routing_registered()
    await test_emu_routing_missing_params()
    await test_emu_routing_train()
    await test_emu_routing_emu_no()
    print("\nemu.routing 测试完成 ✔")


if __name__ == "__main__":
    asyncio.run(main())